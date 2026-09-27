"""Shared pytest fixtures for db/poc SQL view tests.

These fixtures back `tests/test_sql_view_creation.py`, which verifies that
every `db/poc/*.sql` file used by `pipeline.Pipeline.create_views()` is
syntactically valid and can be turned into a working `CREATE VIEW ...`
statement.

There is no real Postgres server available in this test environment, so we
spin up a throw-away Postgres instance with the `pgserver` pip package (a
self-contained Postgres binary, no Docker/system install required) instead
of depending on `db/docker-compose.yaml`.

`pgserver`'s bundled Postgres does NOT ship the `http`, `pg_ivm`, or
`pg_cron` extensions that `db/setting.sql` normally installs -- those are
compiled C extensions that a pure `pip install` cannot provide. However,
`Pipeline.create_views()` only needs the `custom.*` helper functions and
schemas defined in `db/setting.sql` to *exist* so the views' queries can be
parsed/analyzed -- it never actually executes `http_get()` over the network
at `CREATE VIEW` time. So we apply a *sanitized* copy of `db/setting.sql`
that skips the three `CREATE EXTENSION` statements, the two
`http_set_curlopt()` calls, and the two `pg_cron`-backed
`public.job`/`public.job_run_details` views, and executes everything else
(schemas, `custom.*` functions, `mcp_reader` grants) verbatim. This keeps
the test setup in sync with `db/setting.sql` automatically -- if the
`custom.*` helper functions change, tests pick that up for free.

Each skip pattern below is paired with the number of times it is expected
to match. If `db/setting.sql` changes such that a pattern's match count no
longer agrees, `_sanitize_setting_sql` raises `AssertionError` rather than
silently skipping too much/little -- update `_SKIPPED_PATTERNS` when that
happens.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from typing import Iterator, List, Tuple

import psycopg
import pgserver
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTING_SQL_PATH = os.path.join(REPO_ROOT, "db", "setting.sql")

# (regex pattern, expected match count)
_SKIPPED_PATTERNS: List[Tuple[str, int]] = [
    (r"CREATE EXTENSION IF NOT EXISTS\s+http\s*;", 1),
    (r"CREATE EXTENSION IF NOT EXISTS\s+pg_ivm\s*;", 1),
    (r"CREATE EXTENSION IF NOT EXISTS\s+pg_cron\s*;", 1),
    (r"SELECT\s+http_set_curlopt\([^)]*\)\s*;", 2),
    (
        r"CREATE\s+or\s+REPLACE\s+VIEW\s+public\.(job_run_details|job)\b.*?"
        r"GRANT SELECT ON public\.\1 TO mcp_reader;",
        2,
    ),
]

_SKIP_REPLACEMENT = "-- [pytest pgserver mock] skipped: requires an extension unavailable in pgserver"


def _sanitize_setting_sql(sql: str) -> str:
    """Strip extension-dependent statements db/setting.sql needs that the
    pgserver mock cannot provide. Raises AssertionError if db/setting.sql
    has drifted from what this function expects to find/skip.
    """
    sanitized = sql
    for pattern, expected_count in _SKIPPED_PATTERNS:
        matches = re.findall(pattern, sanitized, flags=re.IGNORECASE | re.DOTALL)
        if len(matches) != expected_count:
            raise AssertionError(
                "db/setting.sql 的內容與測試假設的片段不符："
                f"pattern={pattern!r} 預期符合 {expected_count} 次，"
                f"實際符合 {len(matches)} 次。"
                "請更新 tests/conftest.py 的 _SKIPPED_PATTERNS 以反映 db/setting.sql 的最新內容。"
            )
        sanitized = re.sub(
            pattern, _SKIP_REPLACEMENT, sanitized, flags=re.IGNORECASE | re.DOTALL
        )
    return sanitized


@contextmanager
def _pg_tool_setup_noop() -> Iterator[None]:
    """Patch `pg_tool.PostgreSQLTool.setup` to a no-op for the duration of
    the `with` block.

    `Pipeline.__init__` unconditionally calls `PostgreSQLTool().setup()`,
    which re-runs the *unmodified* `db/setting.sql` -- including the
    extension-dependent statements the pgserver mock can't run. The
    sanitized setup is applied once (per test session) by the `pg_dsn`
    fixture instead, so `Pipeline()` construction should skip re-running
    the real file.
    """
    import pg_tool

    original_setup = pg_tool.PostgreSQLTool.setup
    pg_tool.PostgreSQLTool.setup = lambda self: None
    try:
        yield
    finally:
        pg_tool.PostgreSQLTool.setup = original_setup


@pytest.fixture(scope="session")
def pg_server():
    """Session-scoped throw-away Postgres instance backed by `pgserver`."""
    pgdata = tempfile.mkdtemp(prefix="twstock_pgserver_")
    server = pgserver.get_server(pgdata)
    try:
        yield server
    finally:
        server.cleanup()
        shutil.rmtree(pgdata, ignore_errors=True)


@pytest.fixture(scope="session")
def pg_dsn(pg_server) -> str:
    """DSN for an `app_db` database with the sanitized `db/setting.sql`
    already applied."""
    pg_server.psql("CREATE DATABASE app_db;")
    dsn = pg_server.get_uri(database="app_db")

    with open(SETTING_SQL_PATH, "r", encoding="utf-8") as f:
        setting_sql = _sanitize_setting_sql(f.read())

    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(setting_sql)
    finally:
        conn.close()
    return dsn


@pytest.fixture(scope="session")
def pipeline(pg_dsn):
    """A real `pipeline.Pipeline` instance wired to the pgserver-backed
    mock database instead of the hard-coded `localhost:5432` DSN."""
    with _pg_tool_setup_noop():
        import pipeline as pipeline_module

        p = pipeline_module.Pipeline()
    p._db_tool._dsn = pg_dsn
    return p


def pytest_generate_tests(metafunc):
    """Dynamically parametrize any test requesting `sql_path` with every
    `db/poc/*.sql` file, in the same dependency (DAG) order
    `Pipeline.create_views()` uses -- so the test ID names the exact SQL
    file under test."""
    if "sql_path" not in metafunc.fixturenames:
        return

    with _pg_tool_setup_noop():
        import pipeline as pipeline_module

        ordered_paths = pipeline_module.Pipeline().ordered_sql_paths

    metafunc.parametrize("sql_path", ordered_paths, ids=ordered_paths)
