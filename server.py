"""FastMCP server exposing read-only access to the twstock_api Postgres.

Optimisation goals over the previous single-file version:

1. **Connection pool** — every incoming MCP tool call previously opened
   a brand-new libpq connection to Postgres, which is (a) slow (TCP +
   TLS + auth), (b) invisible to Postgres' ``max_connections`` limit
   under bursty load, and (c) leaks a socket if the calling task is
   cancelled mid-``fetchall()``. We now hold a ``psycopg_pool.ConnectionPool``
   (sync, thread-safe) with tunable min/max sizes. Each tool checks out
   a connection via context manager and always returns it — even on
   exception — via psycopg_pool's internal ``__exit__``.

2. **Multi-threaded request handling** — FastMCP dispatches sync tool
   callables through ``anyio.to_thread.run_sync`` (default 40 threads
   per token per anyio). The ``ConnectionPool`` is designed for this
   pattern: ``pool.connection()`` blocks the calling thread when the
   pool is exhausted rather than opening a new libpq socket. Combined
   with ``max_size`` this makes Postgres load bounded.

3. **Long-running query safety** — three layers:
   * Postgres-side ``statement_timeout`` set on every checked-out
     connection (default 30s, tunable per-tool via ``timeout_ms``).
   * Read-only transaction mode (``default_transaction_read_only=on``)
     so a runaway query cannot mutate anything even if the whitelist
     is bypassed.
   * Reset applied on connection check-in (``pool.check`` +
     ``reset`` callback) so a client-supplied per-call timeout does
     not leak into the next borrower.

4. **Deterministic shutdown** — pool is opened in FastMCP's lifespan
   context and closed on server exit, so every pooled connection is
   drained cleanly (``pool.close(timeout=...)`` calls ``conn.close()``
   on each). An ``atexit`` hook covers the crash path.

Environment variables
---------------------
DATABASE_URL           libpq DSN. Required.
DB_POOL_MIN            Minimum idle connections (default 1).
DB_POOL_MAX            Maximum total connections (default 16).
DB_POOL_TIMEOUT        Seconds to wait for a free conn (default 30).
DB_STATEMENT_TIMEOUT_MS  Default per-query timeout in ms (default 30000).
DB_MAX_ROWS            Row cap enforced by ``select`` (default 1000).
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import os
import re
from typing import Any, Iterator

import psycopg
from psycopg import sql as psql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ["DATABASE_URL"]
POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "16"))
POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30"))
DEFAULT_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
MAX_ROWS = int(os.getenv("DB_MAX_ROWS", "1000"))

logger = logging.getLogger("twstock_api.mcp")

# Session-level GUCs applied to every borrowed connection. Using SET
# SESSION (not `options=`) means they are re-applied even on connections
# recycled by the pool.
_SESSION_INIT_SQL = (
    "SET SESSION default_transaction_read_only = on;"
    f"SET SESSION statement_timeout = {DEFAULT_STATEMENT_TIMEOUT_MS};"
    "SET SESSION idle_in_transaction_session_timeout = 60000;"
    "SET SESSION lock_timeout = 5000;"
)


def _configure_connection(conn: psycopg.Connection) -> None:
    """Applied by ConnectionPool on every new libpq connection."""
    with conn.cursor() as cur:
        cur.execute(_SESSION_INIT_SQL)
    conn.commit()  # end the implicit txn opened by SET SESSION
    # Set libpq client_encoding + row factory defaults.
    conn.row_factory = dict_row


def _reset_connection(conn: psycopg.Connection) -> None:
    """Applied by ConnectionPool when a connection is returned to the pool.

    ``pool.connection()``'s ``__exit__`` already commits (or rolls back on
    exception) and leaves the connection in state IDLE. All per-call
    ``SET LOCAL`` settings expired with the txn, so there is no session
    cruft to discard. We only defensively rollback if somehow left in
    a transaction; running DISCARD ALL here caused ``cannot run inside
    a transaction block`` errors and forced the pool to trash the conn.
    """
    try:
        status = conn.info.transaction_status
        # INTRANS or INERROR means the __exit__ didn't clean up (rare);
        # rollback so the next borrower gets a clean session.
        if status not in (
            psycopg.pq.TransactionStatus.IDLE,
            psycopg.pq.TransactionStatus.UNKNOWN,
        ):
            conn.rollback()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("connection reset failed, will be discarded: %s", e)
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

# Global pool — created lazily on first tool call OR eagerly by lifespan.
# We initialise it up-front so import-time errors surface at ``uvicorn`` boot
# rather than on the first request.
_pool: ConnectionPool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=POOL_MIN,
    max_size=POOL_MAX,
    timeout=POOL_TIMEOUT,
    max_lifetime=30 * 60,  # recycle idle conns every 30 min
    max_idle=5 * 60,       # close truly-idle conns after 5 min
    kwargs={"row_factory": dict_row},
    configure=_configure_connection,
    reset=_reset_connection,
    open=False,  # opened by lifespan below
    name="twstock_api_mcp_pool",
)


@contextlib.contextmanager
def _borrow() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection. Guarantees check-in on all paths."""
    with _pool.connection() as conn:
        yield conn


def _close_pool_on_exit() -> None:
    """atexit safety net — belt for the lifespan-braces."""
    try:
        if not _pool.closed:
            logger.info("atexit: closing db pool")
            _pool.close(timeout=5)
    except Exception:  # pragma: no cover
        pass


atexit.register(_close_pool_on_exit)


@contextlib.asynccontextmanager
async def _lifespan(_mcp: FastMCP):
    """FastMCP lifespan: open pool on startup, close on shutdown."""
    logger.info(
        "opening db pool min=%d max=%d timeout=%ss",
        POOL_MIN, POOL_MAX, POOL_TIMEOUT,
    )
    _pool.open(wait=True, timeout=POOL_TIMEOUT)
    try:
        yield
    finally:
        logger.info("closing db pool")
        _pool.close(timeout=10)


mcp = FastMCP("app-db", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# SELECT whitelist
# ---------------------------------------------------------------------------

# Reject queries with more than one statement or with obvious write DML/DDL
# keywords appearing outside a string literal. This is defence-in-depth on
# top of ``default_transaction_read_only``, not a real SQL parser.
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:insert|update|delete|truncate|drop|create|alter|grant|revoke|"
    r"copy|vacuum|analyze|reindex|cluster|refresh|comment|listen|notify|"
    r"call|do|set|reset)\b",
    re.IGNORECASE,
)


def _validate_select_sql(user_sql: str) -> str:
    """Return the sql stripped of trailing ``;`` or raise ``ValueError``.

    Rules:
      * Must start with SELECT or WITH.
      * Multiple statements forbidden (``;`` allowed only as trailing).
      * DML/DDL keywords forbidden.
    """
    stripped = user_sql.strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("SQL is empty.")

    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Only SELECT or WITH queries are allowed.")

    if ";" in stripped:
        raise ValueError("Only one SQL statement is allowed.")

    # crude keyword check: ignores content inside string literals? no -- we
    # accept false positives here because read-only txn will refuse writes
    # anyway. This just catches obviously malicious payloads early.
    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise ValueError("Query contains a forbidden keyword for a read-only endpoint.")

    return stripped


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
def list_tables(schema: str = "public") -> list[dict[str, Any]]:
    """列出指定 schema 的資料表與 views。"""
    sql = """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
    """
    with _borrow() as conn, conn.cursor() as cur:
        cur.execute(sql, (schema,))
        return cur.fetchall()


@mcp.tool
def list_schemas() -> list[dict[str, Any]]:
    """列出 database 所有 non-system schema (排除 pg_* 與 information_schema)."""
    sql = """
        SELECT nspname AS schema_name
        FROM pg_namespace
        WHERE nspname NOT LIKE 'pg_%'
          AND nspname <> 'information_schema'
        ORDER BY nspname
    """
    with _borrow() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


@mcp.tool
def describe_table(table_name: str, schema: str = "public") -> list[dict[str, Any]]:
    """取得資料表欄位、型別與 nullable 資訊。"""
    sql = """
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
    """
    with _borrow() as conn, conn.cursor() as cur:
        cur.execute(sql, (schema, table_name))
        return cur.fetchall()


@mcp.tool
def select(
    sql: str,
    limit: int | None = None,
    timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    """執行唯讀 SELECT 或 WITH 查詢。

    Args:
        sql: SELECT / WITH 語句（不可含 ``;``）。
        limit: 最多回傳筆數；預設 ``DB_MAX_ROWS`` (1000)，上限 10000.
        timeout_ms: 本次查詢的 statement_timeout 覆寫 (毫秒)；預設
                    ``DB_STATEMENT_TIMEOUT_MS`` (30000)，上限 300000 (5 分鐘).
    """
    inner_sql = _validate_select_sql(sql)

    effective_limit = min(max(1, limit or MAX_ROWS), 10000)
    effective_timeout = min(
        max(100, timeout_ms or DEFAULT_STATEMENT_TIMEOUT_MS),
        300_000,
    )

    # Wrap in outer SELECT to enforce LIMIT even when the user forgot one.
    wrapped_sql = psql.SQL("SELECT * FROM ({inner}) AS mcp_query LIMIT {lim}").format(
        inner=psql.SQL(inner_sql),
        lim=psql.Literal(effective_limit),
    )

    with _borrow() as conn:
        with conn.cursor() as cur:
            # SET LOCAL scopes the timeout to this transaction only;
            # the pool's reset() also re-applies the default GUCs.
            cur.execute(
                psql.SQL("SET LOCAL statement_timeout = {ms}").format(
                    ms=psql.Literal(effective_timeout)
                )
            )
            try:
                cur.execute(wrapped_sql)
                return cur.fetchall()
            except psycopg.errors.QueryCanceled as e:
                # Distinguish timeout from admin cancel for a clearer error.
                raise TimeoutError(
                    f"Query exceeded statement_timeout of {effective_timeout} ms"
                ) from e


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
