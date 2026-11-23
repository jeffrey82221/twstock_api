"""FastMCP server for the twstock_api Postgres.

Two groups of tools:

* Read-only data access (``list_schemas`` / ``list_tables`` /
  ``describe_table`` / ``select``): every call goes through
  ``pg_tool.PostgreSQLTool.fetch_all``.
* Crawl control for the ``pop`` schema (``crawl_*``): each seed table
  ``pop.<x>_list`` is filled by a pg_cron job that inserts ``row_cnt`` new
  rows from ``hidden.<x>_list`` every ``period_seconds``; the IMMVs built on
  it then call the API. These tools let an agent read crawl health and set
  or pause each job. Scheduling reuses ``pipeline.Pipeline`` (same SQL and
  job names as ``Pipeline.setup_schedules``) instead of duplicating it.
  ``crawl_set_schedule`` and ``crawl_pause`` are the only tools that write.

Connection lifecycle -- opening, closing, DSN -- is entirely owned by
``pg_tool.py``; this file does not create or hold a ``psycopg`` connection
or pool of its own.

Known limitation (deferred on purpose): ``pg_tool.PostgreSQLTool`` opens
and closes a brand-new connection on every call, so there is no connection
pooling here, no per-query ``statement_timeout``, and no protection against
many concurrent/long-running queries. That is fine for the current "let an
AI read the data" use case; pooling and parallel-query safety will be
revisited separately.

Environment variables
---------------------
DB_MAX_ROWS   Row cap enforced by ``select`` (default 1000).
MCP_TRANSPORT ``stdio`` (default) or ``http``.
PORT          HTTP listen port when MCP_TRANSPORT=http (default 8000).
"""
from __future__ import annotations
import uvicorn
import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import threading
from typing import Any

from fastmcp import FastMCP

from pg_tool import PostgreSQLTool
from pydantic import Field
from typing import Annotated

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
    # The alias must not collide with a column name in ``sql``: with
    # ``AS t``, a query that had its own column ``t`` made ``json_agg(t)``
    # aggregate that column instead of the row.
    wrapped = (
        "SELECT COALESCE(json_agg(_mcp_row), '[]'::json) "
        f"FROM ({sql}) AS _mcp_row"
    )
    rows = _db.fetch_all(wrapped, params)
    if not rows or rows[0][0] is None:
        return []
    return rows[0][0]


# ---------------------------------------------------------------------------
# Tools -- every DB access goes through _db.fetch_all()
# ---------------------------------------------------------------------------


@mcp.tool()
def list_tables(schema_name: Annotated[str, Field(alias='schema', description='Name of the specify table schema for listing its tables')]) -> list[dict[str, Any]]:
    """列出指定 schema 的資料表與 views。"""
    sql = """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %s
        ORDER BY table_name
    """
    return _fetch_as_dicts(sql, (schema_name,))


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
def describe_table(table_name: str,
    schema_name: Annotated[
        str,
        Field(alias="schema", description="要查詢的資料庫 schema 名稱"),
    ] = "public") -> list[dict[str, Any]]:
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
    return _fetch_as_dicts(sql, (schema_name, table_name))


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
# Crawl control: pg_cron jobs that fill pop.<seed>_list
# ---------------------------------------------------------------------------
# Job naming and the per-tick INSERT come from Pipeline.schedule_seed_table_refresh:
# job 'seed_refresh_<table>0' runs
#   INSERT INTO pop.<table> SELECT * FROM hidden.<table> WHERE ... EXCEPT
#   SELECT * FROM pop.<table> LIMIT <row_cnt>;
# (a '...1' job exists only when table_reindex=True put a REINDEX in slot 0).

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_pipeline = None
_pipeline_lock = threading.Lock()


@contextlib.contextmanager
def _stdout_to_stderr():
    """Pipeline prints progress to stdout, which is the MCP stdio channel."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _get_pipeline():
    """Lazily build one Pipeline (parses db/poc/*.sql once; no DB setup)."""
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            with _stdout_to_stderr():
                from pipeline import Pipeline

                p = Pipeline(setup_db=False)
                p.cached_seed_tables = list(p.seed_tables)
            _pipeline = p
    return _pipeline


def _seed_tables() -> list[str]:
    return _get_pipeline().cached_seed_tables


def _check_seed_table(table: str) -> str:
    seeds = _seed_tables()
    if table not in seeds:
        raise ValueError(f"{table!r} is not a seed table. Seed tables: {sorted(seeds)}")
    return table


def _insert_prefix(table: str) -> str:
    # Every seed job's command starts with this; used to match run history
    # even after the job was unscheduled/rescheduled under a new jobid.
    return f"INSERT INTO pop.{table} %"


def _jobs_for(table: str) -> list[dict[str, Any]]:
    return _fetch_as_dicts(
        """
        SELECT jobid, jobname, schedule, active,
               NULLIF(substring(command FROM 'LIMIT ([0-9]+)'), '')::int AS row_cnt,
               command
        FROM cron.job
        WHERE jobname IN (%s, %s)
        ORDER BY jobname
        """,
        (f"seed_refresh_{table}0", f"seed_refresh_{table}1"),
    )


_STATUS_SQL = """
WITH seeds AS (
    SELECT unnest(%s::text[]) AS seed_table
),
jobs AS (
    SELECT s.seed_table, j.jobid, j.schedule, j.active,
           NULLIF(substring(j.command FROM 'LIMIT ([0-9]+)'), '')::int AS row_cnt
    FROM seeds s
    JOIN cron.job j ON j.jobname = 'seed_refresh_' || s.seed_table || '0'
),
runs AS (
    SELECT s.seed_table, d.status, d.start_time, d.end_time, d.return_message,
           CASE WHEN d.status = 'succeeded' AND d.return_message LIKE 'INSERT %%'
                THEN split_part(d.return_message, ' ', 3)::int END AS inserted
    FROM seeds s
    JOIN cron.job_run_details d
      ON d.command LIKE 'INSERT INTO pop.' || s.seed_table || ' %%'
     AND d.start_time > now() - make_interval(mins => %s)
),
agg AS (
    SELECT seed_table,
           count(*) FILTER (WHERE status = 'succeeded') AS runs_ok,
           count(*) FILTER (WHERE status = 'failed') AS runs_failed,
           count(*) FILTER (WHERE status IN ('running', 'starting')) AS runs_in_progress,
           COALESCE(sum(inserted), 0) AS rows_inserted,
           round(avg(extract(epoch FROM end_time - start_time))
                 FILTER (WHERE status = 'succeeded')::numeric, 2) AS avg_run_seconds,
           max(start_time) AS last_run_at
    FROM runs GROUP BY seed_table
),
last_run AS (
    SELECT DISTINCT ON (seed_table) seed_table, status AS last_status,
           left(return_message, 300) AS last_message
    FROM runs ORDER BY seed_table, start_time DESC
),
last_err AS (
    SELECT DISTINCT ON (seed_table) seed_table, start_time AS last_error_at,
           left(return_message, 300) AS last_error
    FROM runs WHERE status = 'failed' ORDER BY seed_table, start_time DESC
)
SELECT s.seed_table,
       j.schedule, j.row_cnt, j.active,
       (j.jobid IS NOT NULL) AS scheduled,
       COALESCE(a.runs_ok, 0) AS runs_ok,
       COALESCE(a.runs_failed, 0) AS runs_failed,
       COALESCE(a.runs_in_progress, 0) AS runs_in_progress,
       COALESCE(a.rows_inserted, 0) AS rows_inserted,
       a.avg_run_seconds,
       a.last_run_at, l.last_status, l.last_message,
       e.last_error_at, e.last_error
FROM seeds s
LEFT JOIN jobs j USING (seed_table)
LEFT JOIN agg a USING (seed_table)
LEFT JOIN last_run l USING (seed_table)
LEFT JOIN last_err e USING (seed_table)
ORDER BY s.seed_table
"""


def _pop_row_counts(tables: list[str]) -> dict[str, int]:
    """Exact count(*) per pop table (only names taken from pg_class)."""
    existing = {
        r["relname"]
        for r in _fetch_as_dicts(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'pop' AND c.relkind = 'r'"""
        )
    }
    names = [t for t in tables if t in existing]
    if not names:
        return {}
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, count(*) AS n FROM pop.\"{t}\"" for t in names
    )
    return {r["t"]: r["n"] for r in _fetch_as_dicts(union)}


@mcp.tool()
def crawl_status(
    lookback_minutes: Annotated[
        int, Field(ge=1, le=10080, description="統計 cron 執行紀錄的時間窗（分鐘）")
    ] = 60,
) -> list[dict[str, Any]]:
    """每個 pop seed table（*_list）的爬取狀況總覽。

    每列一個 seed table：目前 cron 排程（schedule / row_cnt / active）、
    pop 內現有筆數、時間窗內成功/失敗/執行中的次數、實際新增筆數與
    換算的每小時新增量、平均每次執行秒數、最後一次執行結果與最後一次錯誤。

    判讀：scheduled=false 表示沒有排程；runs_ok>0 但 rows_inserted=0
    通常表示 hidden 已沒有新資料可灌（爬完了）；runs_failed 多且
    last_error 是 timeout 表示 row_cnt 太大或上游 API 太慢。
    """
    seeds = _seed_tables()
    rows = _fetch_as_dicts(_STATUS_SQL, (seeds, lookback_minutes))
    counts = _pop_row_counts(seeds)
    for r in rows:
        r["pop_rows"] = counts.get(r["seed_table"])
        r["rows_per_hour"] = round(r["rows_inserted"] * 60.0 / lookback_minutes, 1)
    return rows


@mcp.tool()
def crawl_runs(
    table: Annotated[str, Field(description="seed table 名稱，例如 company_list")],
    limit: Annotated[int, Field(ge=1, le=200)] = 20,
    only_failed: bool = False,
) -> list[dict[str, Any]]:
    """某個 seed table 最近幾次 cron 執行紀錄（新到舊），含耗時、新增筆數與錯誤訊息。"""
    _check_seed_table(table)
    status_filter = "AND status = 'failed'" if only_failed else ""
    return _fetch_as_dicts(
        f"""
        SELECT runid, jobid, status, start_time,
               round(extract(epoch FROM end_time - start_time)::numeric, 2) AS run_seconds,
               CASE WHEN status = 'succeeded' AND return_message LIKE 'INSERT %%'
                    THEN split_part(return_message, ' ', 3)::int END AS inserted,
               left(return_message, 500) AS return_message
        FROM cron.job_run_details
        WHERE command LIKE %s {status_filter}
        ORDER BY start_time DESC
        LIMIT %s
        """,
        (_insert_prefix(table), limit),
    )


@mcp.tool()
def crawl_pop_tables() -> list[dict[str, Any]]:
    """pop schema 每張表的實際筆數：seed table（*_list）與其下游 IMMV（raw_* 與解析後的表）。

    用來看 seed 灌進去之後，下游 materialized view 是否也跟著長出資料。
    """
    rels = _fetch_as_dicts(
        """
        SELECT c.relname AS table_name,
               CASE WHEN EXISTS (SELECT 1 FROM pgivm.pg_ivm_immv i WHERE i.immvrelid = c.oid)
                    THEN 'immv' ELSE 'seed' END AS kind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'pop' AND c.relkind = 'r'
        ORDER BY 2 DESC, 1
        """
    )
    counts = _pop_row_counts([r["table_name"] for r in rels])
    for r in rels:
        r["rows"] = counts.get(r["table_name"])
    return rels


@mcp.tool()
def crawl_recommendations() -> dict[str, Any]:
    """讀取 probe 量測出的建議爬取參數，作為設定排程的參考。

    回傳 throughput_config.json（Pipeline.probe_all_throughput 產生，
    每張表的 max_throughput / min_period / max_batch 三種 (period_seconds,
    row_cnt) 設定）與 batch_size.json（Pipeline.probe_all 產生，每張表
    單次安全 row_cnt）。檔案不存在時對應欄位為 null。
    """
    out: dict[str, Any] = {}
    for key, fname in (("throughput_config", "throughput_config.json"),
                       ("batch_size", "batch_size.json")):
        path = os.path.join(_REPO_DIR, fname)
        if not os.path.exists(path):
            out[key] = None
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if key == "throughput_config":
            # Drop the raw sweep grid; keep the three chosen profiles.
            data = {t: {k: v for k, v in prof.items() if k != "sweep"}
                    for t, prof in data.items()}
        out[key] = data
    out["seed_tables"] = _seed_tables()
    return out


@mcp.tool()
def crawl_set_schedule(
    table: Annotated[str, Field(description="seed table 名稱，例如 company_list")],
    period_seconds: Annotated[int, Field(
        gt=0,
        description=("每隔幾秒執行一次。允許 1..59，或可整除 60 分鐘的分鐘數 "
                     "(60,120,180,240,300,360,600,720,900,1200,1800)，"
                     "或可整除 24 小時的小時數 (3600,7200,...,86400)。"),
    )],
    row_cnt: Annotated[int, Field(ge=1, le=10000, description="每次新增幾筆 seed rows (SQL LIMIT)")],
) -> dict[str, Any]:
    """建立或更新某個 seed table 的 pg_cron 爬取排程（寫入操作）。

    每次執行會從 hidden.<table> 挑 row_cnt 筆 pop 還沒有的資料 insert 進
    pop.<table>，下游 IMMV 因此觸發 API 呼叫。與 Pipeline.setup_schedules
    使用同一個 job 名稱，重複呼叫即覆蓋原設定。建議先看 crawl_recommendations
    的量測值；row_cnt 越大單次越久，超過 API timeout 會整批失敗並在下次重試。
    """
    _check_seed_table(table)
    p = _get_pipeline()
    # Validates the period (raises ValueError with the allowed forms).
    schedule = p._period_seconds_to_schedule(period_seconds)
    with _stdout_to_stderr():
        p.schedule_seed_table_refresh(table=table, period_seconds=period_seconds, row_cnt=row_cnt)
    return {"table": table, "schedule": schedule, "row_cnt": row_cnt, "jobs": _jobs_for(table)}


@mcp.tool()
def crawl_pause(
    table: Annotated[str, Field(description="seed table 名稱，例如 company_list")],
) -> dict[str, Any]:
    """停止某個 seed table 的爬取排程（unschedule 其 pg_cron job；寫入操作）。

    已灌進 pop 的資料不受影響；之後可用 crawl_set_schedule 重新開始。
    """
    _check_seed_table(table)
    removed = []
    for job in _jobs_for(table):
        _db.execute_query("SELECT cron.unschedule(%s);", (job["jobid"],))
        removed.append(job["jobname"])
    return {"table": table, "unscheduled_jobs": removed, "remaining_jobs": _jobs_for(table)}


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
    # Default is stdio (what Claude Code launches). MCP_TRANSPORT=http
    # serves over HTTP on 0.0.0.0:$PORT instead.
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    run_kwargs: dict[str, Any] = {}
    if transport != "stdio":
        run_kwargs["transport"] = transport
        if not hasattr(mcp, "settings"):
            run_kwargs["host"] = "0.0.0.0"
            run_kwargs["port"] = int(os.getenv("PORT", "8000"))

    mcp.run(**run_kwargs)