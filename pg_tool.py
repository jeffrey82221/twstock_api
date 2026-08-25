"""Database helper tool wrapping a psycopg connection pool.

Single source of truth for all Postgres access in twstock_api. Both the
data pipeline (``pipeline.py``) and the MCP server (``server.py``) go
through ``PostgreSQLTool`` so connection lifecycle is owned in exactly one
place — no caller is allowed to open a ``psycopg`` connection directly.

Backward compatibility
---------------------
``PostgreSQLTool()`` (no args) keeps the original behaviour expected by
``pipeline.py``:

* writable (no ``default_transaction_read_only``),
* tuple-row output (``fetch_all`` returns ``list[tuple]``; pipeline.py
  indexes ``row[0]`` / ``result[0][0]``),
* DSN defaults to the localhost ``app_db`` used on the dev Mac.

New constructor kwargs let the MCP server request an isolated read-only
pool with per-session timeouts and dict-row output, without affecting
the writable pool used by the pipeline:

* ``dsn`` — override the connection string (server.py passes ``DATABASE_URL``).
* ``read_only=True`` — sets ``default_transaction_read_only = on`` on every
  pooled connection so a runaway/whitelist-bypassing query cannot mutate.
* ``statement_timeout_ms`` — sets a session-level ``statement_timeout``
  (plus ``idle_in_transaction_session_timeout`` / ``lock_timeout``).
* ``row_factory`` — ``dict_row`` for MCP JSON output, ``tuple_row`` (default)
  for pipeline.py.
* ``pool_min`` / ``pool_max`` / ``pool_timeout`` — pool sizing.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import RowFactory, dict_row, tuple_row
from psycopg_pool import ConnectionPool


logger = logging.getLogger("twstock_api.pg_tool")


class PostgreSQLTool:
    """A database helper tool backed by a ``psycopg_pool.ConnectionPool``.

    The pool is created lazily on first use (``_get_pool``) and shared
    across all calls on the same instance, so callers do not pay the
    TCP+auth cost of a fresh libpq connection per query. Each instance owns
    its own pool; ``server.py`` (read-only, dict rows) and ``pipeline.py``
    (writable, tuple rows) therefore get isolated pools that do not
    interfere with each other's session GUCs.
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        read_only: bool = False,
        statement_timeout_ms: int | None = None,
        row_factory: RowFactory = tuple_row,
        pool_min: int = 1,
        pool_max: int = 16,
        pool_timeout: float = 30.0,
    ) -> None:
        # NOTE: do NOT auto-read DATABASE_URL here — pipeline.py relies on the
        # hardcoded localhost DSN when no arg is passed. server.py passes an
        # explicit ``dsn`` (typically from DATABASE_URL) to opt into env config.
        self._dsn = dsn or "postgresql://postgres:postgres@localhost:5432/app_db"
        self._read_only = read_only
        self._statement_timeout_ms = statement_timeout_ms
        self._row_factory = row_factory
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool_timeout = pool_timeout
        self._pool: ConnectionPool | None = None
        self._pool_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    def _configure_session_sql(self) -> str:
        """Session GUCs applied once when a new libpq connection is checked
        out for the first time. ``SET SESSION`` (not ``SET LOCAL``) so the
        values persist across transactions on the same pooled connection."""
        stmts: list[str] = []
        if self._read_only:
            stmts.append("SET SESSION default_transaction_read_only = on;")
        if self._statement_timeout_ms:
            stmts.append(
                f"SET SESSION statement_timeout = {int(self._statement_timeout_ms)};"
            )
            stmts.append("SET SESSION idle_in_transaction_session_timeout = 60000;")
            stmts.append("SET SESSION lock_timeout = 5000;")
        return "".join(stmts)

    def _get_pool(self) -> ConnectionPool:
        # Double-checked locking: fast path reads the pool without taking the
        # lock, slow path takes the lock so concurrent first-callers don't
        # each create their own ConnectionPool (the last one would win and
        # the others would leak their connections with no live close() hook).
        if self._pool is not None and not self._pool.closed:
            return self._pool
        with self._pool_lock:
            if self._pool is not None and not self._pool.closed:
                return self._pool
            configure_sql = self._configure_session_sql()
            row_factory = self._row_factory

            def configure(conn: psycopg.Connection) -> None:
                if configure_sql:
                    with conn.cursor() as cur:
                        cur.execute(configure_sql)
                    conn.commit()
                # Force Unicode text decoding regardless of the server's
                # database encoding — psycopg returns ``bytes`` for text
                # columns when client_encoding is ASCII (e.g. a DB
                # initialised with the SQL_ASCII locale). Mac/production DBs
                # are UTF8 so this is a no-op there.
                try:
                    conn.execute("SET client_encoding = 'UTF8'")
                    conn.commit()
                except Exception:
                    # A SET failure leaves the transaction aborted — roll
                    # back so the connection is still usable for a retry.
                    logger.warning(
                        "SET client_encoding failed; rolling back", exc_info=True
                    )
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                conn.row_factory = row_factory

            def reset(conn: psycopg.Connection) -> None:
                # ``pool.connection()``'s ``__exit__`` already commits or
                # rolls back and leaves the connection IDLE. Per-call
                # ``SET LOCAL`` values expired with the txn, so there is no
                # session cruft to DISCARD. Only defensively roll back if
                # somehow left inside a transaction (rare).
                try:
                    status = conn.info.transaction_status
                    if status not in (
                        TransactionStatus.IDLE,
                        TransactionStatus.UNKNOWN,
                    ):
                        conn.rollback()
                except Exception as e:
                    logger.warning(
                        "connection reset failed, will be discarded: %s", e
                    )
                    try:
                        conn.close()
                    except Exception:
                        pass

            self._pool = ConnectionPool(
                conninfo=self._dsn,
                min_size=self._pool_min,
                max_size=self._pool_max,
                timeout=self._pool_timeout,
                max_lifetime=30 * 60,
                max_idle=5 * 60,
                kwargs={"row_factory": row_factory},
                configure=configure,
                reset=reset,
                open=True,
                name=f"pg_tool_pool{'_ro' if self._read_only else ''}",
            )
            return self._pool

    @property
    def pool(self) -> ConnectionPool:
        """The underlying pool (created lazily on first access)."""
        return self._get_pool()

    def close(self) -> None:
        """Close the pool, draining every pooled connection cleanly."""
        with self._pool_lock:
            if self._pool is not None and not self._pool.closed:
                self._pool.close(timeout=10)

    # ------------------------------------------------------------------
    # Query helpers — the only DB access surface used by callers
    # ------------------------------------------------------------------

    def execute_query(self, query: str, params: tuple | None = None) -> None:
        """Execute a query (INSERT/UPDATE/DDL) and commit.

        Supports multi-statement strings (e.g. ``SET ...; <stmt>; RESET ...``)
        because ``cursor.execute`` runs the whole string and ``conn.commit``
        commits the implicit transaction. Used by ``pipeline.py`` for writes.
        """
        pool = self._get_pool()
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params)
                conn.commit()
        except Exception as e:
            logger.error("execute_query failed: %s", e)
            raise

    def fetch_all(self, query: str, params: tuple | None = None) -> list:
        """Execute a single-statement query and return all rows as tuples.

        Backward-compatible with ``pipeline.py`` which indexes ``row[0]``.
        Note: unlike ``execute_query``, this does not reliably support
        multi-statement strings — psycopg3 only exposes the LAST executed
        command's result via ``cur.fetchall()``, and if that last command
        has no result set (e.g. a trailing ``SET``), fetching raises
        ``ProgrammingError`` instead of returning the SELECT's rows. Pass a
        single SELECT/etc. statement per call.
        """
        pool = self._get_pool()
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
        except Exception as e:
            logger.error("fetch_all failed: %s", e)
            raise

    def fetch_all_dicts(
        self,
        query: str,
        params: tuple | None = None,
        *,
        statement_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a query and return all rows as dicts (column name -> value).

        Used by the MCP server so tool results serialise to JSON objects.
        Overrides the cursor's row factory to ``dict_row`` per call.

        ``statement_timeout_ms`` (when given) issues a ``SET LOCAL
        statement_timeout`` *before* the query on the same cursor. Because
        the pool wraps the body in a single transaction (``with conn:``),
        the ``SET LOCAL`` stays in effect for the query and expires
        automatically at transaction end — no per-call value leaks to the
        next borrower. The value is a validated ``int`` owned by this method,
        so inlining it as a literal is injection-safe.
        """
        pool = self._get_pool()
        try:
            with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                if statement_timeout_ms is not None:
                    cur.execute(
                        f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"
                    )
                cur.execute(query, params)
                return cur.fetchall()
        except Exception as e:
            logger.error("fetch_all_dicts failed: %s", e)
            raise

    def setup(self) -> None:
        """Apply ``db/setting.sql`` (extensions + schemas)."""
        pool = self._get_pool()
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                with open("db/setting.sql", "r") as f:
                    cur.execute(f.read())
                conn.commit()
        except Exception as e:
            logger.error("database setup failed: %s", e)
            raise
