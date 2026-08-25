"""FastMCP server exposing read-only access to the twstock_api Postgres.

All database access is delegated to :mod:`pg_tool` (``PostgreSQLTool``).
This module owns **no** ``psycopg`` connection of its own — it only:

1. validates and wraps user-supplied SQL,
2. maps a per-call ``timeout_ms`` into the query via ``SET LOCAL``,
3. exposes four MCP tools (``list_tables``, ``list_schemas``,
   ``describe_table``, ``select``).

The read-only / timeout / pool configuration lives entirely in
``PostgreSQLTool`` (see ``pg_tool.py``), so connection lifecycle has a
single source of truth shared with ``pipeline.py``.

Environment variables
---------------------
``DATABASE_URL``           libpq DSN passed to ``PostgreSQLTool``.
``DB_POOL_MIN``            Min idle pooled connections (default 1).
``DB_POOL_MAX``            Max total pooled connections (default 16).
``DB_POOL_TIMEOUT``       Seconds to wait for a free conn (default 30).
``DB_STATEMENT_TIMEOUT_MS``  Default per-query timeout in ms (default 30000).
``DB_MAX_ROWS``            Row cap enforced by ``select`` (default 1000).
``PORT``                   HTTP listen port (default 8000).
"""
from __future__ import annotations

import atexit
import contextlib
import inspect
import logging
import os
import re
from typing import Any

import psycopg
from fastmcp import FastMCP

from pg_tool import PostgreSQLTool


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
MAX_ROWS = int(os.getenv("DB_MAX_ROWS", "1000"))
POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "16"))
POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30"))

logger = logging.getLogger("twstock_api.mcp")

# The single DB handle for the whole server. Read-only + session timeouts are
# applied to every pooled connection by PostgreSQLTool.configure; the MCP tools
# never touch a psycopg.Connection directly.
_db = PostgreSQLTool(
    dsn=os.environ.get("DATABASE_URL"),
    read_only=True,
    statement_timeout_ms=DEFAULT_STATEMENT_TIMEOUT_MS,
    pool_min=POOL_MIN,
    pool_max=POOL_MAX,
    pool_timeout=POOL_TIMEOUT,
)


def _close_db() -> None:
    """atexit safety net for the pool."""
    try:
        _db.close()
    except Exception:  # pragma: no cover - defensive
        pass


atexit.register(_close_db)


@contextlib.asynccontextmanager
async def _lifespan(_mcp: FastMCP):
    """Open the pool on startup, close it on shutdown."""
    # Touching .pool forces lazy creation so a misconfigured DSN surfaces now,
    # not on the first tool call.
    _ = _db.pool
    logger.info(
        "db pool ready (read_only, min=%d max=%d timeout=%ss statement_timeout=%dms)",
        POOL_MIN, POOL_MAX, POOL_TIMEOUT, DEFAULT_STATEMENT_TIMEOUT_MS,
    )
    try:
        yield
    finally:
        logger.info("closing db pool")
        _db.close()


mcp_kwargs: dict[str, Any] = {"name": "app-db"}
if "lifespan" in inspect.signature(FastMCP.__init__).parameters:
    # FastMCP 2.x / 3.x support a server lifespan for startup/shutdown hooks.
    mcp_kwargs["lifespan"] = _lifespan
mcp = FastMCP(**mcp_kwargs)

if not "lifespan" in mcp_kwargs:
    # FastMCP 1.0 (or any version without lifespan support): eagerly open the
    # pool now so a bad DSN surfaces at import time instead of on first call.
    # atexit (registered above) still drains it on shutdown.
    _ = _db.pool


# ---------------------------------------------------------------------------
# SELECT whitelist (defence-in-depth on top of the read-only transaction)
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(?:insert|update|delete|truncate|drop|create|alter|grant|revoke|"
    r"copy|vacuum|analyze|reindex|cluster|refresh|comment|listen|notify|"
    r"call|do|set|reset)\b",
    re.IGNORECASE,
)


def _validate_select_sql(user_sql: str) -> str:
    """Return the SQL stripped of a trailing ``;`` or raise ``ValueError``.

    Rules:
      * Must start with SELECT or WITH.
      * Only one statement (no ``;`` inside).
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

    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise ValueError("Query contains a forbidden keyword for a read-only endpoint.")

    return stripped


# ---------------------------------------------------------------------------
# Tools — every DB access goes through _db (PostgreSQLTool)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_tables(schema: str = "public") -> list[dict[str, Any]]:
    """列出指定 schema 的資料表與 views。"""
    sql = """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
    """
    return _db.fetch_all_dicts(sql, (schema,))


@mcp.tool()
def list_schemas() -> list[dict[str, Any]]:
    """列出 database 所有 non-system schema (排除 pg_* 與 information_schema)."""
    sql = """
        SELECT nspname AS schema_name
        FROM pg_namespace
        WHERE nspname NOT LIKE 'pg_%'
          AND nspname <> 'information_schema'
        ORDER BY nspname
    """
    return _db.fetch_all_dicts(sql)


@mcp.tool()
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
    return _db.fetch_all_dicts(sql, (schema, table_name))


@mcp.tool()
def select(
    sql: str,
    limit: int | None = None,
    timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    """執行唯讀 SELECT 或 WITH 查詢。

    Args:
        sql: SELECT / WITH 語句（不可含 ``;``）。
        limit: 最多回傳筆數；預設 ``DB_MAX_ROWS`` (1000)，上限 10000。
        timeout_ms: 本次查詢的 statement_timeout 覆寫 (毫秒)；預設
                    ``DB_STATEMENT_TIMEOUT_MS`` (30000)，上限 300000 (5 分鐘)。
    """
    inner_sql = _validate_select_sql(sql)

    effective_limit = min(max(1, limit or MAX_ROWS), 10000)
    effective_timeout = min(
        max(100, timeout_ms or DEFAULT_STATEMENT_TIMEOUT_MS),
        300_000,
    )

    # Wrap the validated user SQL in an outer SELECT to enforce a LIMIT
    # even when the caller forgot one. ``effective_limit`` is a validated
    # int owned by this function, so inlining it as a literal is
    # injection-safe; ``inner_sql`` has already passed the SELECT/WITH +
    # no-``;`` + no-DML/DDL whitelist, with the read-only transaction as
    # the final backstop.
    wrapped_select = (
        f"SELECT * FROM ({inner_sql}) AS mcp_query LIMIT {effective_limit}"
    )

    try:
        return _db.fetch_all_dicts(
            wrapped_select,
            statement_timeout_ms=effective_timeout,
        )
    except psycopg.errors.QueryCanceled as e:
        # statement_timeout fired server-side; surface a clear error.
        raise TimeoutError(
            f"Query exceeded statement_timeout of {effective_timeout} ms"
        ) from e


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
# fastmcp 1.0 exposes a ``settings`` attribute (host/port); fastmcp 3.x does
# not and instead takes ``host``/``port`` directly as ``run()`` kwargs.
# Support both so the same file runs against either pinned version.
if hasattr(mcp, "settings"):
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.getenv("PORT", "8000"))

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_kwargs = {"transport": "sse"}
    if not hasattr(mcp, "settings"):
        run_kwargs["host"] = "0.0.0.0"
        run_kwargs["port"] = int(os.getenv("PORT", "8000"))
    mcp.run(**run_kwargs)
