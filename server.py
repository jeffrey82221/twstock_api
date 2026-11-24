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
* Schema maintenance: ``poc_rebuild_views`` rebuilds the whole poc schema;
  ``pop_check_definitions`` / ``pop_update_mat_views`` compare pop with the
  current SQL and update only the objects that changed
  (``Pipeline.pop_definition_drift`` / ``Pipeline.update_mat_views``).

Tools that write: ``crawl_set_schedule``, ``crawl_pause``,
``poc_rebuild_views``, and -- with ``dry_run=false`` -- ``crawl_insert_filtered``,
``crawl_delete_rows``, ``http_cache_evict`` and ``pop_update_mat_views``.
The re-crawl helpers behind ``crawl_delete_rows`` / ``http_cache_evict`` live in
``pop_maintenance.py``.

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
def crawl_backlog(
    table: Annotated[str, Field(description="seed table 名稱，例如 company_list")],
    row_cnt: Annotated[int, Field(ge=1, le=10000, description="模擬一次 tick 取幾筆")] = 1,
    timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 60,
) -> dict[str, Any]:
    """量測某個 seed table 還剩多少待爬，以及每次 cron tick 選列（不含 insert/API）的 DB 成本（唯讀）。

    每次 tick 都要算出整個 hidden.<table> 再 EXCEPT pop.<table>，所以 tick_select_ms
    會隨母體與已爬筆數成長；pending 是目前 hidden 有、pop 沒有、且無 NULL 欄位的筆數。
    注意：hidden.chain_list 會呼叫 API（其餘 hidden view 為純 SQL）。
    """
    import time

    _check_seed_table(table)
    p = _get_pipeline()
    with _stdout_to_stderr():
        insert_sql = p._build_seed_insert_sql(table, row_cnt=row_cnt)[-1]
    select_sql = insert_sql.split(f"INSERT INTO pop.{table} ", 1)[1].rstrip(";")
    without_limit = select_sql.rsplit(" LIMIT ", 1)[0]
    conn = _db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{int(timeout_seconds)}s'")
            t0 = time.perf_counter()
            cur.execute(select_sql)
            picked = len(cur.fetchall())
            tick_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            cur.execute(f"SELECT count(*) FROM ({without_limit}) AS pending")
            pending = cur.fetchone()[0]
            pending_ms = (time.perf_counter() - t0) * 1000
            cur.execute(f'SELECT count(*) FROM pop."{table}"')
            done = cur.fetchone()[0]
    finally:
        conn.rollback()
        conn.close()
    return {
        "table": table,
        "pop_rows": done,
        "pending": pending,
        "tick_select_ms": round(tick_ms, 1),
        "rows_picked_by_tick": picked,
        "full_pending_count_ms": round(pending_ms, 1),
    }


@mcp.tool()
def crawl_insert_filtered(
    table: Annotated[str, Field(description="seed table 名稱，例如 company_list")],
    filters: Annotated[
        dict[str, str | int | float | bool | list[str | int | float | bool]],
        Field(description=(
            "hidden.<table> 欄位篩選，{column: value} 表示 column = value；"
            "value 為 list 時表示 column IN (...)。多個欄位以 AND 結合。"
            "例如 {\"stock_id\": \"2330\"} 或 {\"stock_id\": [\"2330\", \"2317\"]}。"
            "欄位名稱可用 describe_table(pop, <table>) 查詢。"
        )),
    ],
    row_cnt: Annotated[int, Field(ge=1, le=10000, description="最多 insert 幾筆 (SQL LIMIT)")] = 10,
    order_by: Annotated[
        list[str] | None,
        Field(description=(
            "決定 LIMIT 留下哪幾筆，格式 [\"column\", \"column ASC|DESC\", ...]；"
            "只寫欄位名稱時為 DESC（最新優先）。省略（預設）時自動用該表第一個 "
            "date/timestamp 欄位（否則 year/ym/quarter/month）DESC，即最新優先；"
            "傳 [] 表示不排序。例如 [\"year_start_date\"]、[\"month_start_date ASC\"]。"
        )),
    ] = None,
    dry_run: Annotated[
        bool, Field(description="true（預設）只回傳符合條件的待爬筆數與 SQL；確認後再以 false 執行")
    ] = True,
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 120,
) -> dict[str, Any]:
    """依條件優先把 hidden.<table> 的特定資料 insert 進 pop.<table>（dry_run=false 時為寫入操作）。

    與 cron 排程使用同一個 INSERT（Pipeline._build_seed_insert_sql），只是多了
    WHERE column = value 的條件，讓 AI 做資料分析時能先爬與當下問題最相關的資料
    （例如特定股票），而不用等排程依序輪到；order_by 決定先爬哪幾筆（預設最新優先）。
    只會挑 pop 還沒有、且無 NULL 欄位的列；
    下游 IMMV 會因此觸發 API 呼叫，row_cnt 越大越久，超過 timeout_seconds 整批 rollback。
    """
    import time

    _check_seed_table(table)
    p = _get_pipeline()
    with _stdout_to_stderr():
        if order_by is None:
            order_by = p._default_seed_order_by(table)
        insert_sql = p._build_seed_insert_sql(
            table, row_cnt=row_cnt, filters=filters, order_by=order_by)[-1]
        # Pending count needs neither ORDER BY nor LIMIT.
        count_sql = p._build_seed_insert_sql(table, row_cnt=1, filters=filters)[-1]
    without_limit = count_sql.split(f"INSERT INTO pop.{table} ", 1)[1].rstrip(";").rsplit(" LIMIT ", 1)[0]

    conn = _db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{int(timeout_seconds)}s'")
            cur.execute(f"SELECT count(*) FROM ({without_limit}) AS pending")
            pending = cur.fetchone()[0]
            result: dict[str, Any] = {
                "table": table,
                "filters": filters,
                "order_by": order_by,
                "pending_matching": pending,
                "sql": insert_sql,
                "dry_run": dry_run,
            }
            if dry_run or pending == 0:
                conn.rollback()
                result["inserted"] = 0
                return result
            t0 = time.perf_counter()
            cur.execute(insert_sql)
            inserted = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    result["inserted"] = inserted
    result["insert_seconds"] = round(time.perf_counter() - t0, 2)
    result["pending_after"] = max(pending - inserted, 0)
    return result


@mcp.tool()
def crawl_delete_rows(
    table: Annotated[str, Field(description="seed table 名稱，例如 sbl_history_monthly_list")],
    filters: Annotated[
        dict[str, str | int | float | bool | list[str | int | float | bool]],
        Field(description=(
            "pop.<table> 欄位篩選，格式同 crawl_insert_filtered；不可為空。"
            "例如 {\"stk_code\": \"2330\", \"month_start_date\": [\"2015-07-05\", \"2015-08-05\"]}。"
        )),
    ],
    dry_run: Annotated[
        bool, Field(description="true（預設）只回傳符合筆數、樣本與下游 IMMV；確認後再以 false 執行")
    ] = True,
    pin_responses: Annotated[
        bool, Field(description="刪除前把 raw_* 已存的回應暫存進 http_cache（預設 true，見下方說明）")
    ] = True,
    allow_unverified: Annotated[
        bool, Field(description="下游沒有 1:1 的 IMMV 可驗證時仍執行（預設 false，會 rollback）")
    ] = False,
    timeout_seconds: Annotated[int, Field(ge=1, le=600)] = 120,
) -> dict[str, Any]:
    """刪除 pop.<table> 中符合條件的 seed 列，供重爬使用（dry_run=false 時為寫入操作）。

    pg_ivm 刪除時會對被刪的列重跑下游 IMMV 的查詢（含 API 呼叫），再刪掉逐欄位相符的
    IMMV 列；API 現在的回應若和當初 insert 時不同（重爬的情境幾乎都是如此），IMMV 列會
    刪不掉而變成孤兒（IMMV 不允許直接 DML）。因此本 tool 在同一個交易內：
    (1) pin：把下游 raw_* 已存的回應依 URL 寫進 http_cache，讓 pg_ivm 重算出相同結果；
    (2) DELETE；(3) 驗證每個與 seed 1:1 的 IMMV 剛好少了被刪的列數，否則整批 rollback；
    (4) 移除 pin 的快取，讓之後 crawl_insert_filtered 重爬時抓即時資料。
    單次上限 500 列；執行期間鎖住該 seed，cron insert 會等待。dry_run 也會回報可 pin 的筆數。
    """
    import pop_maintenance

    _check_seed_table(table)
    p = _get_pipeline()
    conn = _db.get_conn()
    try:
        with _stdout_to_stderr():
            return pop_maintenance.delete_seed_rows(
                conn, p, table, filters, dry_run=dry_run, timeout_seconds=timeout_seconds,
                pin_responses=pin_responses, allow_unverified=allow_unverified)
    finally:
        conn.close()


@mcp.tool()
def http_cache_evict(
    url_like: Annotated[str, Field(description=(
        "SQL LIKE 樣式，須含 '/api/' 與股票代號或日期（4 位以上數字），"
        "例如 '%/api/company/2330/sbl-history?as_of=2015-%'"
    ))],
    dry_run: Annotated[
        bool, Field(description="true（預設）只回傳符合筆數、status 分布與樣本 URL")
    ] = True,
) -> dict[str, Any]:
    """刪除 job_control.http_cache 中 url 符合 url_like 的快取（dry_run=false 時為寫入操作）。

    2xx 與 4xx 回應會被快取（TTL 見 job_control.http_config.cache_ttl_seconds，預設 3600 秒）。
    API 修好後 1 小時內重爬會拿到舊結果，所以重爬前先清。crawl_delete_rows 已自動清掉它
    pin 過的 URL；本 tool 用於其他情況（例如還沒進 pop 的列、修好 API 後要重打的 URL）。
    單次上限 5000 筆。
    """
    import pop_maintenance

    conn = _db.get_conn()
    try:
        return pop_maintenance.evict_http_cache(conn, url_like, dry_run=dry_run)
    finally:
        conn.close()


@mcp.tool()
def http_stats(
    minutes: Annotated[int, Field(ge=1, le=10080, description="統計時間窗（分鐘）")] = 60,
) -> dict[str, Any]:
    """API 呼叫統計（唯讀，來源 job_control.http_log；cache 命中不會進 log）。

    每個 endpoint：實際請求數、不重複 URL 數、重複請求數（同 URL 打超過一次 = 浪費的網路）、
    2xx/404/5xx 筆數、平均與最大秒數。另附 http_cache 目前筆數。
    逾時等傳輸錯誤會讓整筆 insert rollback，所以不會留在 log，要看 crawl_runs 的錯誤。
    """
    endpoints = _fetch_as_dicts(
        """
        SELECT regexp_replace(
                 regexp_replace(regexp_replace(url, '^https?://[^/]+', ''), '\\?.*$', ''),
                 '/[0-9]{4,6}(/|$)', '/{id}\\1', 'g') AS endpoint,
               count(*) AS requests,
               count(DISTINCT url) AS distinct_urls,
               count(*) - count(DISTINCT url) AS repeated_requests,
               count(*) FILTER (WHERE status_code BETWEEN 200 AND 299) AS ok_2xx,
               count(*) FILTER (WHERE status_code = 404) AS not_found_404,
               count(*) FILTER (WHERE status_code >= 500) AS server_5xx,
               round(avg(extract(epoch FROM finished_at - started_at))::numeric, 2) AS avg_seconds,
               round(max(extract(epoch FROM finished_at - started_at))::numeric, 1) AS max_seconds
        FROM job_control.http_log
        WHERE started_at > now() - make_interval(mins => %s)
        GROUP BY 1
        ORDER BY requests DESC
        """,
        (minutes,),
    )
    cache = _fetch_as_dicts(
        "SELECT count(*) AS entries, count(*) FILTER (WHERE status_code BETWEEN 200 AND 299) AS ok_entries "
        "FROM job_control.http_cache"
    )
    return {"minutes": minutes, "endpoints": endpoints, "cache": cache[0] if cache else None}


@mcp.tool()
def pop_raw_quality() -> list[dict[str, Any]]:
    """每個 pop.raw_* IMMV 的資料可用率（唯讀）。

    對每個 jsonb 欄位統計：總列數、NULL（API 非 2xx 或回應非 JSON）、found=false
    （API 回 200 但沒資料，例如上游限流或該日期本來就沒資料）、可用列數，以及 source 分佈。
    seed row 一旦插入，IMMV 的這一列就固定了，不會自動重抓，所以低可用率代表浪費的爬取。
    """
    cols = _fetch_as_dicts(
        """
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'pop' AND c.table_name LIKE 'raw\\_%%' AND c.data_type = 'jsonb'
        ORDER BY 1, 2
        """
    )
    if not cols:
        return []
    parts = []
    for c in cols:
        t, col = c["table_name"], c["column_name"]
        parts.append(
            f"""SELECT '{t}' AS raw_view, '{col}' AS json_column,
                   count(*) AS total_rows,
                   count(*) FILTER (WHERE "{col}" IS NULL) AS null_rows,
                   count(*) FILTER (WHERE "{col}"->>'found' = 'false') AS found_false_rows,
                   count(*) FILTER (WHERE "{col}" IS NOT NULL
                                    AND COALESCE("{col}"->>'found', 'true') <> 'false') AS usable_rows,
                   (SELECT json_object_agg(s, n) FROM (
                        SELECT COALESCE("{col}"->>'source', '(none)') AS s, count(*) AS n
                        FROM pop."{t}" GROUP BY 1) src) AS sources
            FROM pop."{t}" """
        )
    rows = _fetch_as_dicts(" UNION ALL ".join(parts))
    for r in rows:
        r["usable_pct"] = round(100.0 * r["usable_rows"] / r["total_rows"], 1) if r["total_rows"] else None
    return rows


def _fresh_pipeline():
    """A Pipeline that re-reads db/poc/*.sql (the cached one may predate SQL edits)."""
    with _stdout_to_stderr():
        from pipeline import Pipeline

        return Pipeline(setup_db=False)


@mcp.tool()
def pop_check_definitions(
    names: Annotated[
        list[str] | None,
        Field(description="只檢查這些物件（seed table 或 IMMV 名稱）；省略則檢查全部"),
    ] = None,
    only_drifted: bool = True,
) -> list[dict[str, Any]]:
    """比對 pop schema 現有物件與目前 db/poc/*.sql 的定義是否一致（唯讀）。

    seed table（*_list）比對 hidden.<name> view 定義；其他比對 pop.<name> IMMV 定義。
    status：ok / changed（SQL 改過，需要 update）/ missing（不存在）/
    invalid_sql（新 SQL 在 pop 上編譯失敗，通常是上游要先更新）。
    改完 SQL 後先跑 poc_rebuild_views，再用這個看哪些 pop 物件需要更新。
    """
    p = _fresh_pipeline()
    with _stdout_to_stderr():
        rows = p.pop_definition_drift(names)
    return [r for r in rows if not only_drifted or r["status"] != "ok"]


@mcp.tool()
def pop_update_mat_views(
    names: Annotated[
        list[str] | None,
        Field(description="只更新這些物件；省略則更新所有 status 非 ok 的物件"),
    ] = None,
    dry_run: Annotated[
        bool, Field(description="true（預設）只回傳計畫；確認後再以 false 執行")
    ] = True,
) -> list[dict[str, Any]]:
    """只更新定義有變動的 pop 物件，不重建整個 pop schema（寫入操作，dry_run=false 時）。

    - seed table：CREATE OR REPLACE VIEW hidden.<name>，pop.<name> 已爬的資料保留；
      欄位有變動時拒絕執行（需刪資料，rule 18）。
    - IMMV：DROP + pgivm.create_immv 重建，會對現有 seed rows 重新跑一次查詢
      （每列一次 API 呼叫，http_cache 內的會直接命中），被 CASCADE 掉的 hidden view 會補回。
    dry_run 的結果中 recreate_immv 會列出 seed_upstreams，可用 crawl_status 的
    pop_rows 估計要重打的 API 次數。執行前建議先暫停相關 crawl 排程。
    """
    p = _fresh_pipeline()
    with _stdout_to_stderr():
        return p.update_mat_views(names, dry_run=dry_run)


@mcp.tool()
def poc_rebuild_views() -> dict[str, Any]:
    """整個重建 poc schema 的 view（DROP SCHEMA poc CASCADE 後依 DAG 順序重建；寫入操作）。

    poc 是研發層、沒有存資料，可以安全整個重建。pop 不受影響。
    """
    p = _fresh_pipeline()
    with _stdout_to_stderr():
        p.create_views()
    views = _fetch_as_dicts(
        "SELECT count(*) AS n FROM information_schema.views WHERE table_schema = 'poc'")
    return {"poc_views": views[0]["n"], "sql_files": len(p.ordered_sql_paths)}


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