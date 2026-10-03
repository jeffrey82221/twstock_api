"""Pytest coverage for `pg_tool.PostgreSQLTool`.

Postgres is mocked entirely with `pgembed` (https://pypi.org/project/pgembed/)
-- a self-contained, pip-installable Postgres build with no `sudo`/system
install required, so contributors without a local Postgres can run this
suite. Requires the version pinned in `requirements.txt`
(`pgembed==0.1.6`): that is the only published release whose wheel bundles
`pg_duckdb.so` (see `test_pg_duckdb_read_json_from_url` below) -- versions
0.1.7 onward dropped it. If `pgembed` is missing or this wheel's build
doesn't include `pg_duckdb`, the whole module is skipped with a clear
reason rather than failing.

## Why `db/setting.sql` needs stub extensions, not real ones

`PostgreSQLTool.setup()` executes the *entire, unmodified*
`db/setting.sql` in one call. That file does::

    CREATE EXTENSION IF NOT EXISTS http;
    CREATE EXTENSION IF NOT EXISTS pg_ivm;
    CREATE EXTENSION IF NOT EXISTS pg_cron;
    SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '15000');
    ...
    SELECT * FROM cron.job_run_details;   -- (inside a CREATE VIEW)
    SELECT * FROM cron.job;                -- (inside a CREATE VIEW)

None of `http`, `pg_ivm`, `pg_cron` ship in the `pgembed` wheel (unlike
`pgvector`/`pgtextsearch`/`pg_search`/`pg_duckdb`, which do). Compiling the
real `http`, `pg_ivm`, and `pg_cron` extensions from source would need a
C toolchain, and `pg_cron` additionally needs `shared_preload_libraries`
plus a real background worker -- overkill for what this test actually
needs to verify, which is that `PostgreSQLTool.setup()` reads and executes
`db/setting.sql` correctly (schemas, helper functions, role, views), not
that IVM/cron actually schedule anything.

So `_register_stub_extensions()` below installs three tiny, pure-SQL
"stub" extensions directly into pgembed's own shared extension directory
(the same `pginstall/` tree previous work in this repo compiled the real
`http` extension into for a different test suite -- see
`tests/test_poc_view_select_one_row.py` on the `feature/
lineagex-auto-visualization` branch):

- `http`: only defines `http_set_curlopt(text, text) returns boolean` as a
  trivial SQL function. Nothing in `db/setting.sql` calls `http_get()` at
  the top level -- it's only referenced inside `custom
  .http_get_content_logged`'s `plpgsql` body, and `plpgsql` bodies are
  opaque strings that Postgres does not validate at `CREATE FUNCTION`
  time (only when actually called) -- so the stub never needs to
  implement `http_get()` itself.
- `pg_ivm`: a no-op (empty SQL script). `db/setting.sql` never calls an
  IVM function directly; comments mention IVM but no code does.
- `pg_cron`: creates a `cron` schema with empty `cron.job` /
  `cron.job_run_details` tables, just enough for
  `db/setting.sql`'s `CREATE VIEW public.job AS SELECT * FROM cron.job`
  (and `job_run_details`) to succeed.

This is idempotent and installed once per pgembed install location, no
compiler required.
"""

from __future__ import annotations

import os
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

pgembed = pytest.importorskip("pgembed", reason="pip install pgembed==0.1.6")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTING_SQL_PATH = os.path.join(REPO_ROOT, "db", "setting.sql")

from pg_tool import PostgreSQLTool  # noqa: E402  (after importorskip)

PG_DUCKDB_SO = "pg_duckdb.so"
SAMPLE_API_URL = "https://jsonplaceholder.typicode.com/todos/1"


def _pginstall_dir() -> Path:
    return Path(pgembed.__file__).resolve().parent / "pginstall"


def _extension_dir() -> Path:
    return _pginstall_dir() / "share" / "postgresql" / "extension"


def _has_pg_duckdb() -> bool:
    return (_pginstall_dir() / "lib" / "postgresql" / PG_DUCKDB_SO).exists()


def _register_stub_extensions() -> None:
    """Install no-op `http` / `pg_ivm` / `pg_cron` extensions into
    pgembed's own `pginstall/share/postgresql/extension/` tree -- see the
    module docstring for why these are safe stand-ins for
    `db/setting.sql`'s purposes. Idempotent (always (re)writes the same
    fixed content).
    """
    ext_dir = _extension_dir()
    ext_dir.mkdir(parents=True, exist_ok=True)

    (ext_dir / "http.control").write_text(
        "comment = 'stub http extension for pg_tool tests (no real HTTP calls)'\n"
        "default_version = '1.0'\n"
        "relocatable = false\n"
    )
    (ext_dir / "http--1.0.sql").write_text(
        "CREATE FUNCTION http_set_curlopt(text, text) RETURNS boolean\n"
        "LANGUAGE sql AS $$ SELECT true $$;\n"
    )

    (ext_dir / "pg_ivm.control").write_text(
        "comment = 'stub pg_ivm extension for pg_tool tests (no incremental views)'\n"
        "default_version = '1.0'\n"
        "relocatable = false\n"
    )
    (ext_dir / "pg_ivm--1.0.sql").write_text("-- stub: no-op\n")

    (ext_dir / "pg_cron.control").write_text(
        "comment = 'stub pg_cron extension for pg_tool tests (no real scheduling)'\n"
        "default_version = '1.0'\n"
        "relocatable = false\n"
    )
    (ext_dir / "pg_cron--1.0.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS cron;\n"
        "CREATE TABLE IF NOT EXISTS cron.job (\n"
        "    jobid    bigint PRIMARY KEY,\n"
        "    schedule text,\n"
        "    command  text,\n"
        "    nodename text,\n"
        "    nodeport int,\n"
        "    database text,\n"
        "    username text,\n"
        "    active   boolean,\n"
        "    jobname  text\n"
        ");\n"
        "CREATE TABLE IF NOT EXISTS cron.job_run_details (\n"
        "    jobid         bigint,\n"
        "    runid         bigint PRIMARY KEY,\n"
        "    job_pid       int,\n"
        "    database      text,\n"
        "    username      text,\n"
        "    command       text,\n"
        "    status        text,\n"
        "    return_message text,\n"
        "    start_time    timestamptz,\n"
        "    end_time      timestamptz\n"
        ");\n"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_server() -> Iterator["pgembed.PostgresServer"]:
    """One embedded Postgres for the whole module (no preloaded
    extensions) -- cheaper than re-running `initdb` per test. Individual
    tests get isolation via the `pg_tool` fixture, which recreates
    `app_db` from scratch before each test.
    """
    _register_stub_extensions()
    pgdata = tempfile.mkdtemp(prefix="pgembed_pg_tool_")
    server = pgembed.get_server(pgdata, cleanup_mode="delete")
    try:
        yield server
    finally:
        server.cleanup()
        shutil.rmtree(pgdata, ignore_errors=True)


@pytest.fixture
def pg_tool(pg_server: "pgembed.PostgresServer") -> Iterator[PostgreSQLTool]:
    """A `PostgreSQLTool` pointed at a freshly (re)created `app_db` on
    `pg_server`. `PostgreSQLTool.__init__` hard-codes its DSN to
    `.../app_db` on `localhost`, so we override `_dsn` after construction
    to point at the embedded server instead -- the class has no other way
    to take a custom DSN.
    """
    admin_dsn = pg_server.get_uri(database="postgres")
    conn = psycopg.connect(admin_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS app_db;")
            cur.execute("CREATE DATABASE app_db;")
    finally:
        conn.close()

    tool = PostgreSQLTool()
    tool._dsn = pg_server.get_uri(database="app_db")
    yield tool


# ---------------------------------------------------------------------------
# get_conn
# ---------------------------------------------------------------------------


def test_get_conn_returns_live_connection(pg_tool: PostgreSQLTool):
    conn = pg_tool.get_conn()
    try:
        assert conn.closed == 0
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            assert cur.fetchone() == (1,)
    finally:
        conn.close()


def test_get_conn_raises_on_unreachable_dsn():
    tool = PostgreSQLTool()
    # Port 1 is a reserved low port nothing listens on as Postgres.
    tool._dsn = "postgresql://nouser:nopass@127.0.0.1:1/does_not_exist"
    with pytest.raises(Exception):
        tool.get_conn()


# ---------------------------------------------------------------------------
# execute_query / fetch_all
# ---------------------------------------------------------------------------


def test_execute_query_commits_and_fetch_all_reads_it_back(pg_tool: PostgreSQLTool):
    pg_tool.execute_query("CREATE TABLE widgets (id int PRIMARY KEY, name text);")
    pg_tool.execute_query(
        "INSERT INTO widgets (id, name) VALUES (%s, %s);", (1, "left-handed screwdriver")
    )
    pg_tool.execute_query(
        "INSERT INTO widgets (id, name) VALUES (%s, %s);", (2, "sky hook")
    )

    rows = pg_tool.fetch_all("SELECT id, name FROM widgets ORDER BY id;")
    assert rows == [(1, "left-handed screwdriver"), (2, "sky hook")]


def test_execute_query_commit_is_visible_from_a_separate_connection(pg_tool: PostgreSQLTool):
    pg_tool.execute_query("CREATE TABLE committed_rows (x int);")
    pg_tool.execute_query("INSERT INTO committed_rows (x) VALUES (%s);", (7,))

    # A brand-new connection (not reusing pg_tool's internal handle) must
    # see the committed row -- proves execute_query() really commits
    # rather than leaving the transaction open.
    other_conn = psycopg.connect(pg_tool._dsn)
    try:
        with other_conn.cursor() as cur:
            cur.execute("SELECT x FROM committed_rows;")
            assert cur.fetchall() == [(7,)]
    finally:
        other_conn.close()


def test_fetch_all_returns_empty_list_for_empty_table(pg_tool: PostgreSQLTool):
    pg_tool.execute_query("CREATE TABLE nothing_here (x int);")
    assert pg_tool.fetch_all("SELECT * FROM nothing_here;") == []


def test_fetch_all_with_params(pg_tool: PostgreSQLTool):
    pg_tool.execute_query("CREATE TABLE colors (id int, name text);")
    pg_tool.execute_query("INSERT INTO colors (id, name) VALUES (1, 'red'), (2, 'blue');")
    rows = pg_tool.fetch_all("SELECT name FROM colors WHERE id = %s;", (2,))
    assert rows == [("blue",)]


def test_execute_query_raises_on_invalid_sql(pg_tool: PostgreSQLTool):
    with pytest.raises(Exception):
        pg_tool.execute_query("SELECT * FROM a_table_that_does_not_exist;")


def test_fetch_all_raises_on_invalid_sql(pg_tool: PostgreSQLTool):
    with pytest.raises(Exception):
        pg_tool.fetch_all("SELECT * FROM another_missing_table;")


def test_execute_query_and_fetch_all_close_the_connection_each_call(pg_tool: PostgreSQLTool):
    """`execute_query`/`fetch_all` both close `self._conn` and reset it to
    None in their `finally` blocks -- confirms that internal bookkeeping
    so a leaked connection handle can't accumulate across repeated calls.
    """
    pg_tool.execute_query("CREATE TABLE t1 (x int);")
    assert pg_tool._conn is None
    pg_tool.fetch_all("SELECT * FROM t1;")
    assert pg_tool._conn is None


# ---------------------------------------------------------------------------
# setup()
# ---------------------------------------------------------------------------


def test_setup_executes_real_setting_sql_end_to_end(pg_tool: PostgreSQLTool):
    """Runs the actual, unmodified `db/setting.sql` (via the stub
    extensions registered by the `pg_server` fixture) and checks the
    concrete objects it is supposed to leave behind: schemas, the
    `custom.*` helper functions, the `mcp_reader` role, and the
    `public.job` / `public.job_run_details` views over the (stub)
    `cron` schema.
    """
    assert os.path.exists(SETTING_SQL_PATH), f"expected {SETTING_SQL_PATH} to exist"

    pg_tool.setup()

    schemas = {
        row[0]
        for row in pg_tool.fetch_all(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name = ANY(%s);",
            (["poc", "pop", "hidden", "custom", "job_control"],),
        )
    }
    assert schemas == {"poc", "pop", "hidden", "custom", "job_control"}

    custom_functions = {
        row[0]
        for row in pg_tool.fetch_all(
            "SELECT proname FROM pg_proc WHERE pronamespace = 'custom'::regnamespace;"
        )
    }
    assert {
        "parse_iso_date",
        "trunc_year",
        "date_to_iso",
        "http_get_content_logged",
        "http_get_content",
        "jsonb_array_elements",
    } <= custom_functions

    roles = pg_tool.fetch_all("SELECT rolname FROM pg_roles WHERE rolname = 'mcp_reader';")
    assert roles == [("mcp_reader",)]

    views = {
        row[0]
        for row in pg_tool.fetch_all(
            "SELECT viewname FROM pg_views WHERE viewname = ANY(%s);",
            (["job", "job_run_details"],),
        )
    }
    assert views == {"job", "job_run_details"}

    # custom.parse_iso_date is a real (non-stubbed) IMMUTABLE SQL function
    # -- exercise it to confirm setup() didn't just create an empty shell.
    result = pg_tool.fetch_all("SELECT custom.parse_iso_date('2026-09-28');")
    assert result == [(__import__("datetime").date(2026, 9, 28),)]


def test_setup_is_idempotent(pg_tool: PostgreSQLTool):
    """`db/setting.sql` uses `IF NOT EXISTS` / `CREATE OR REPLACE`
    throughout, so running `setup()` twice against the same database must
    not raise.
    """
    pg_tool.setup()
    pg_tool.setup()  # should not raise
    roles = pg_tool.fetch_all("SELECT rolname FROM pg_roles WHERE rolname = 'mcp_reader';")
    assert roles == [("mcp_reader",)]


# ---------------------------------------------------------------------------
# pg_duckdb demo: SELECT a real internet API's JSON response via SQL
# ---------------------------------------------------------------------------


def _internet_reachable(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def duckdb_pg_server() -> Iterator["pgembed.PostgresServer"]:
    """A *separate* embedded Postgres from `pg_server`, because
    `shared_preload_libraries` is a postmaster-context setting that can
    only take effect on the *first* start of a given `pgdata` directory --
    it can't be turned on for an already-running instance without a
    restart. Rather than stop/edit/restart the shared `pg_server`
    instance (and risk interfering with the other tests in this module),
    this fixture writes `shared_preload_libraries = 'pg_duckdb'` into
    `postgresql.conf` *before* the very first start of its own dedicated
    `pgdata`.
    """
    if not _has_pg_duckdb():
        pytest.skip(
            "this pgembed build has no pg_duckdb.so -- pin pgembed==0.1.6 "
            "(0.1.7+ dropped pg_duckdb from the published wheel)"
        )

    pgdata_parent = tempfile.mkdtemp(prefix="pgembed_duckdb_")
    pgdata = Path(pgdata_parent) / "pgdata"
    pgdata.mkdir()

    # initdb only -- do not start the postmaster yet, so we can edit
    # postgresql.conf first (mirrors what PostgresServer.ensure_pgdata_inited
    # does internally when PG_VERSION is missing).
    pgembed.initdb(
        ["--auth=trust", "--auth-local=trust", "--encoding=utf8", "-U", "postgres"],
        pgdata=pgdata,
    )
    with open(pgdata / "postgresql.conf", "a") as f:
        f.write("\nshared_preload_libraries = 'pg_duckdb'\n")

    server = pgembed.get_server(pgdata, cleanup_mode="delete")
    try:
        yield server
    finally:
        server.cleanup()
        shutil.rmtree(pgdata_parent, ignore_errors=True)


def test_pg_duckdb_read_json_from_url(duckdb_pg_server: "pgembed.PostgresServer"):
    """Demo: with `pg_duckdb` enabled, plain SQL can `SELECT` a JSON
    response fetched live from a public internet API (DuckDB's bundled
    `httpfs` support) -- no application-side HTTP client or JSON parsing
    needed, just `read_json(url)` in a `FROM` clause.

    Target: https://jsonplaceholder.typicode.com/todos/1, a well-known,
    stable "fake API for testing" endpoint that always returns the same
    fixed JSON object -- ideal for a demo that asserts exact values.
    """
    if not _internet_reachable("jsonplaceholder.typicode.com"):
        pytest.skip("no internet access to jsonplaceholder.typicode.com from this environment")

    conn = psycopg.connect(duckdb_pg_server.get_uri())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_duckdb;")

            try:
                cur.execute(f"SELECT * FROM read_json('{SAMPLE_API_URL}');")
            except psycopg.errors.OperationalError as exc:
                pytest.skip(f"network call to sample API failed: {exc}")

            columns = [d.name for d in cur.description]
            row = cur.fetchone()
    finally:
        conn.close()

    assert columns == ["userId", "id", "title", "completed"]
    assert row == (1, 1, "delectus aut autem", False)
