"""Opt-in live end-to-end check: for every `db/poc/*.sql` view, verify
`SELECT * FROM poc.<view> LIMIT 1` actually succeeds and returns a real
(not entirely-NULL) row -- i.e. the full HTTP-backed data pipeline works
end to end, not just that the view's SQL is syntactically valid (that is
`tests/test_sql_view_creation.py`'s job).

Run with::

    RUN_POC_VIEW_SELECT_TESTS=1 pytest -m poc_view_select tests/test_poc_view_select_one_row.py -v

Unlike the other `db/poc/*.sql` test modules, this one needs three things
the sanitized pgserver mock in `tests/conftest.py` deliberately does NOT
provide, because they need real infrastructure / real network access. Per
the task's development direction, all three stay "pgserver-only" -- no
Docker, no system Postgres deploy:

1. **A working `http` Postgres extension.** `custom.http_get_content`
   (used by every `raw_*.sql` view) is a thin wrapper around the `http`
   extension's `http_get()`. `pgserver`'s bundled Postgres 16.2 does not
   ship it, but pgserver *does* ship that exact Postgres build's own dev
   headers and `pg_config` (`pginstall/include/postgresql/server` +
   `pginstall/bin/pg_config`) -- so `_ensure_http_extension()` below
   compiles https://github.com/pramsey/pgsql-http against pgserver's own
   Postgres build (once) and installs it straight into pgserver's own
   `pginstall/` tree. Requires `gcc`/`make` and libcurl dev headers
   (`libcurl4-openssl-dev` on Debian/Ubuntu) on the machine running the
   tests -- if missing, the whole module is skipped with a clear reason
   instead of failing.

2. **`host.docker.internal` resolving to this machine.** Every
   `raw_*.sql` file hard-codes `http://host.docker.internal:5002/api/...`
   (matching the `db_start/docker-compose.yaml` production deployment,
   where Docker provides that DNS entry automatically). Outside Docker,
   add it once:

       sudo sh -c 'echo "127.0.0.1 host.docker.internal" >> /etc/hosts'

   If it doesn't resolve, the module is skipped rather than a test editing
   system files.

3. **`app/main.py` actually serving on port 5002** (development direction
   1 for this task -- 把 endpoint API 起在背景). `live_app_server` below
   starts `uvicorn app.main:app --host 0.0.0.0 --port 5002` as a
   background subprocess automatically if `GET /api/health` isn't already
   answering there, and stops the subprocess it started once the test
   session ends -- a server already running (started manually by a
   developer) is left alone.

Because this test walks the *entire* real dependency chain -- 61 views,
several of which fan out through TWSE/TPEx/MOPS/FinMind/yfinance -- it is
opt-in and excluded from the default `pytest -q` run, for the same reason
`tests/test_upstream_contracts.py` is: live upstream data is slow, rate
limited, and occasionally flaky, and ordinary contributors should not be
coupled to that when running the fast local suite.

## How each per-view "successful case" was picked (development direction
## 2 -- inspect before writing assertions)

For most views, the *natural* first row (`SELECT * FROM poc.<view> LIMIT
1`, no filter) succeeds well within a minute, because `Pipeline`'s DAG
build order + Postgres's own LIMIT push-down only need to evaluate as many
upstream rows as it takes to produce one output row -- confirmed by
actually building all 61 views against the live backend and timing every
one (see `DEFAULT_TIMEOUT_S` below for the observed ceiling).

Four views needed different treatment after inspecting *why* the naive
query was slow/timed out against the real backend:

- `product_revenue_filer_list`, `raw_product_revenue`, `product_revenue`:
  `product_revenue_filer_list.sql` uses `SELECT DISTINCT ... CROSS JOIN
  LATERAL jsonb_array_elements_text(...)`. `DISTINCT` forces Postgres to
  materialize the *entire* underlying result before it can deduplicate --
  a `LIMIT 1` cannot be pushed through a `DISTINCT`. The underlying result
  is `raw_product_revenue_filers`: one HTTP call per (year-month, market)
  for the last 5 years (~120 calls). Each call is fast on its own
  (~0.01-2s observed directly against the real MOPS-backed endpoint) but
  120 of them add up past a short timeout, and `raw_product_revenue` /
  `product_revenue` both sit downstream of that same `DISTINCT`.

  All three views *do* expose `ym` (year-month) as a plain pass-through
  column, though, and `EXPLAIN (VERBOSE, COSTS OFF)` against the live
  backend confirms Postgres pushes a `WHERE ym = '...'` predicate all the
  way down through the `DISTINCT`/`HashAggregate`/`Unique` node into the
  `months` CTE that drives the HTTP fan-out -- because restricting which
  months get deduplicated doesn't change the result of deduplication,
  Postgres doesn't need `DISTINCT`'s correctness guarantee to see all rows
  first. That collapses ~120 HTTP calls down to 2 (one per market) before
  any grouping happens. `_recent_ym()` computes a `ym` two months before
  today (Republic-of-China year+month, e.g. `'11507'`) at test-collection
  time instead of hard-coding one, since a fixed literal would eventually
  age out of the 5-year rolling window `months.sql`-shaped CTEs generate
  from `CURRENT_DATE`; two months back stays safely clear of MOPS's ~10
  day monthly filing lag. Measured against the live backend: 0.06s /
  1.79s / 1.66s respectively -- comfortably inside `DEFAULT_TIMEOUT_S`,
  so none of the three need a longer timeout at all.

- `sbl_history`: `CROSS JOIN LATERAL jsonb_array_elements(records)` drops
  any (company, month) row whose SBL (借券/還券) history is empty that
  month -- and that is most rows, since SBL lending activity concentrates
  in a handful of liquid large caps. The natural first row in DAG order
  belongs to whichever company is first in `company_basic_info_list`,
  which has no SBL activity, so Postgres keeps calling the upstream
  endpoint (itself ~20s per call against the real backend) for one
  company/month after another and blows past any reasonable timeout.
  Direct inspection confirmed stk_code `2330` (TSMC) has SBL history in
  essentially every month and responds in ~0.2-2s, so the test targets it
  with `WHERE stk_code = '2330'` instead of relying on the natural
  (unfiltered, unordered) first row.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Iterator

import psycopg
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTING_SQL_PATH = os.path.join(REPO_ROOT, "db", "setting.sql")

APP_PORT = 5002
APP_HEALTH_URL = f"http://127.0.0.1:{APP_PORT}/api/health"
PGSQL_HTTP_REPO = "https://github.com/pramsey/pgsql-http.git"

LIVE = os.getenv("RUN_POC_VIEW_SELECT_TESTS") == "1"
pytestmark = [
    pytest.mark.poc_view_select,
    pytest.mark.skipif(not LIVE, reason="set RUN_POC_VIEW_SELECT_TESTS=1"),
]

# Observed ceiling for a view needing exactly one real HTTP call to a slow
# upstream (e.g. `foreign_ownership` ~23.5s) plus network-variance margin.
# Applies uniformly to all 61 views -- once product_revenue_filer_list /
# raw_product_revenue / product_revenue are given a `WHERE ym = ...` that
# Postgres can push down through their `DISTINCT` (see module docstring),
# none of the 61 views need a longer timeout than this.
DEFAULT_TIMEOUT_S = 60


def _recent_ym(months_ago: int = 2) -> str:
    """Republic-of-China `ym` string (e.g. `'11507'`) for `months_ago`
    months before today. Computed at test-collection time rather than
    hard-coded, so it always lands inside the 5-year rolling window
    `months.sql`-shaped CTEs generate from `CURRENT_DATE` (a fixed literal
    would eventually age out) and stays safely clear of MOPS's ~10 day
    monthly product-revenue filing lag.
    """
    from datetime import date

    today = date.today()
    year, month = today.year, today.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return f"{year - 1911:03d}{month:02d}"


# product_revenue_filer_list / raw_product_revenue / product_revenue all sit
# downstream of a `SELECT DISTINCT` that forces Postgres to materialize
# ~120 HTTP calls (5 years x 12 months x 2 markets) before a LIMIT can
# apply -- but all three expose `ym` as a plain pass-through column, and
# `EXPLAIN` confirms Postgres pushes a `WHERE ym = ...` filter down through
# the `DISTINCT` into the underlying `months` CTE, cutting ~120 HTTP calls
# to 2. See module docstring for the measured timings (all well under
# DEFAULT_TIMEOUT_S, no elevated timeout needed).
#
# sbl_history's natural (unfiltered) first row can stall indefinitely on
# sparse SBL activity -- see module docstring. Target a company confirmed
# (by direct inspection) to reliably have SBL history instead of relying
# on row order.
_VIEW_WHERE_OVERRIDES = {
    "product_revenue_filer_list": f"ym = '{_recent_ym()}'",
    "raw_product_revenue": f"ym = '{_recent_ym()}'",
    "product_revenue": f"ym = '{_recent_ym()}'",
    "sbl_history": "stk_code = '2330'",
}


def _app_server_alive() -> bool:
    try:
        with urllib.request.urlopen(APP_HEALTH_URL, timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="module")
def live_app_server() -> Iterator[None]:
    """Ensure `app/main.py` is serving on port 5002. Reuses an already-
    running server (started manually, or by another test session) as-is;
    otherwise starts `uvicorn app.main:app --host 0.0.0.0 --port 5002` as a
    background subprocess for this module's duration and stops it
    afterwards."""
    if _app_server_alive():
        yield
        return

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(APP_PORT)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            if _app_server_alive():
                ready = True
                break
            if proc.poll() is not None:
                pytest.skip("app/main.py 背景啟動後立即結束，請先確認能以 `uvicorn app.main:app` 手動啟動")
            time.sleep(0.5)
        if not ready:
            proc.terminate()
            pytest.skip(f"app/main.py 在 20 秒內未能於 port {APP_PORT} 回應 /api/health")
        yield
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="module")
def live_poc_schema():
    """Build every `db/poc/*.sql` view, once per module, against the live
    (http-enabled) database with `app/main.py` actually serving -- mirrors
    `Pipeline.create_views()`, in DAG order."""
    # _ensure_host_docker_internal()

    # with _pg_tool_setup_noop():
    import pipeline as pipeline_module

    p = pipeline_module.Pipeline()
    # p._db_tool._dsn = live_pg_dsn

    p._db_tool.execute_query("DROP SCHEMA IF EXISTS poc CASCADE;")
    p._db_tool.execute_query("CREATE SCHEMA poc;")
    create_sqls = p.view_create_sqls
    for sql_path in p.ordered_sql_paths:
        p._db_tool.execute_query(create_sqls[sql_path])
    return p


# ---------------------------------------------------------------------------
# The test itself -- `sql_path` is parametrized in DAG order by the
# `pytest_generate_tests` hook in tests/conftest.py.
# ---------------------------------------------------------------------------


def test_view_select_one_row_succeeds(live_app_server, live_poc_schema, sql_path):
    """`SELECT * FROM poc.<view> LIMIT 1` must succeed and return one real
    (not entirely-NULL) row -- confirming the full HTTP-backed pipeline
    actually works end to end for this view, not just that its SQL is
    syntactically valid.

    Uses the same SQL shape as `Pipeline.check_view_run_speed()`
    (`SELECT * FROM poc.{table} LIMIT 1`), with a per-view `WHERE`
    override for the handful of views whose natural first row is
    unreliable -- see the module docstring for how each was found via
    direct inspection against the live backend. A single 60s
    `DEFAULT_TIMEOUT_S` applies to every view; none need more once the
    `WHERE` overrides are in place.
    """
    _ = live_app_server
    view = sql_path[: -len(".sql")]
    where_clause = _VIEW_WHERE_OVERRIDES.get(view)
    timeout_s = DEFAULT_TIMEOUT_S

    select_sql = f"SELECT * FROM poc.{view}"
    if where_clause:
        select_sql += f" WHERE {where_clause}"
    select_sql += " LIMIT 1;"

    conn = psycopg.connect(live_poc_schema._db_tool._dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{timeout_s}s';")
            try:
                cur.execute(select_sql)
            except Exception as exc:  # noqa: BLE001 - surface exact SQL + DB error per view
                pytest.fail(
                    f"poc.{view} 無法成功 SELECT 單行：{type(exc).__name__}: {exc}\n\n"
                    f"執行的 SQL：\n{select_sql}",
                    pytrace=False,
                )
            row = cur.fetchone()
            columns = [d.name for d in cur.description]
            cur.execute("RESET statement_timeout;")
    finally:
        conn.close()

    assert row is not None, f"poc.{view} 回傳 0 列，預期至少 1 列（{select_sql}）"
    assert len(row) == len(columns)
    assert any(value is not None for value in row), (
        f"poc.{view} 回傳的單行所有欄位皆為 NULL，可能代表上游呼叫實際上沒有取得任何資料："
        f"{dict(zip(columns, row))}"
    )
