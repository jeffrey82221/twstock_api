"""FastMCP server exposing read-only access to the twstock_api Postgres.

This is a deliberately simple wrapper: every tool calls
``pg_tool.PostgreSQLTool.fetch_all`` (``execute_query`` is available too but
none of the read-only tools below need it). Connection lifecycle -- opening,
closing, DSN -- is entirely owned by ``pg_tool.py``; this file does not
create or hold a ``psycopg`` connection or pool of its own.

Known limitation (deferred on purpose): ``pg_tool.PostgreSQLTool`` opens
and closes a brand-new connection on every call, so there is no connection
pooling here, no per-query ``statement_timeout``, and no protection against
many concurrent/long-running queries. That is fine for the current "let an
AI read the data" use case; pooling and parallel-query safety will be
revisited separately.

Environment variables
---------------------
DB_MAX_ROWS   Row cap enforced by ``select`` (default 1000).
PORT          HTTP listen port (default 8000).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastmcp import FastMCP

from pg_tool import PostgreSQLTool


MAX_ROWS = int(os.getenv("DB_MAX_ROWS", "1000"))

logger = logging.getLogger("twstock_api.mcp")

# Single PostgreSQLTool instance shared by all tools below. Its DSN and
# connection lifecycle are entirely defined in pg_tool.py -- this file only
# ever calls its fetch_all()/execute_query() methods, never a raw
# psycopg.Connection.
_db = PostgreSQLTool()

mcp = FastMCP("app-db")


# ---------------------------------------------------------------------------
# SELECT whitelist (defence-in-depth; pg_tool.py's connections are not
# forced read-only, so this Python-side check is the only guard for the
# free-form `select` tool).
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


def _fetch_as_dicts(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """Run ``sql`` via ``pg_tool.fetch_all`` and return dict rows.

    ``pg_tool.PostgreSQLTool.fetch_all`` returns plain tuples with no
    column names attached (it doesn't expose ``cursor.description``), and
    we are not touching ``pg_tool.py`` to add that. Instead we let Postgres
    do the naming: wrap the query in ``json_agg`` so it comes back as a
    single JSON value, which psycopg3 auto-parses into a native Python
    ``list[dict]`` -- no pg_tool.py changes needed.
    """
    wrapped = f"SELECT COALESCE(json_agg(t), '[]'::json) FROM ({sql}) AS t"
    rows = _db.fetch_all(wrapped, params)
    if not rows or rows[0][0] is None:
        return []
    return rows[0][0]


# ---------------------------------------------------------------------------
# Tools -- every DB access goes through _db.fetch_all()
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
    return _fetch_as_dicts(sql, (schema,))


@mcp.tool()
def list_schemas() -> list[dict[str, Any]]:
    """列出 database 所有 non-system schema (排除 pg_* 與 information_schema)。"""
    sql = """
        SELECT nspname AS schema_name
        FROM pg_namespace
        WHERE nspname NOT LIKE 'pg_%'
          AND nspname <> 'information_schema'
        ORDER BY nspname
    """
    return _fetch_as_dicts(sql)


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
    return _fetch_as_dicts(sql, (schema, table_name))


@mcp.tool()
def select(sql: str, limit: int | None = None) -> list[dict[str, Any]]:
    """執行唯讀 SELECT 或 WITH 查詢。

    Args:
        sql: SELECT / WITH 語句（不可含 ``;``）。
        limit: 最多回傳筆數；預設 ``DB_MAX_ROWS`` (1000)，上限 10000。

    注意：這是簡化版本，沒有 connection pool、沒有 per-query
    statement_timeout、也沒有 read-only transaction 強制 -- 安全性完全
    依賴上面的 SQL 白名單檢查。長查詢與大量平行查詢的保護會在之後
    另外處理。
    """
    inner_sql = _validate_select_sql(sql)
    effective_limit = min(max(1, limit or MAX_ROWS), 10000)
    limited_sql = f"SELECT * FROM ({inner_sql}) AS mcp_query LIMIT {effective_limit}"
    return _fetch_as_dicts(limited_sql)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
# fastmcp 1.0 exposes a ``settings`` attribute (host/port); fastmcp 3.x does
# not and instead takes ``host``/``port`` directly as ``run()`` kwargs.
# Support both so the same file runs against either pinned version -- the
# repo's requirements.txt currently pins both (``fastmcp[tasks]`` and
# ``fastmcp==1.0``).
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
