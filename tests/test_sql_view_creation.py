"""Verify every `db/poc/*.sql` file can be turned into a working
`CREATE VIEW` statement -- the same check `pipeline.Pipeline.create_views()`
performs when it (re)builds the whole `poc` schema.

Unlike `Pipeline.create_views()`, which executes all views in one loop and
stops at the first exception, this test creates one view per test case (in
the same dependency/DAG order) so a broken SQL file is pinpointed by its own
test ID and the exact failing SQL/DB error, instead of only surfacing the
first failure and hiding the rest.

See `tests/conftest.py` for how the mock Postgres database (via the
`pgserver` pip package) is set up, and why `db/setting.sql` is applied in a
sanitized form.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def poc_schema(pipeline):
    """Reset the `poc` schema once per test module. Individual
    `test_view_can_be_created` cases then build up the schema in
    dependency order, mirroring `Pipeline.create_views()` but issuing one
    `CREATE VIEW` per test."""
    pipeline._db_tool.execute_query("DROP SCHEMA IF EXISTS poc CASCADE;")
    pipeline._db_tool.execute_query("CREATE SCHEMA poc;")
    return pipeline



def test_create_view_all(poc_schema):
    """`db/poc/{sql_path}` must produce a valid `CREATE VIEW` statement.

    Views are created in the same dependency order `Pipeline.create_views()`
    uses. If an upstream view already failed in an earlier test case, a
    downstream view referencing it will fail too (e.g. "relation ... does
    not exist") -- when several consecutive test IDs fail, check the first
    one for the actual root cause.
    """
    poc_schema.create_views()
    

def test_view_can_be_created(poc_schema, sql_path):
    """`db/poc/{sql_path}` must produce a valid `CREATE VIEW` statement.

    Views are created in the same dependency order `Pipeline.create_views()`
    uses. If an upstream view already failed in an earlier test case, a
    downstream view referencing it will fail too (e.g. "relation ... does
    not exist") -- when several consecutive test IDs fail, check the first
    one for the actual root cause.
    """
    create_sql = poc_schema.view_create_sqls[sql_path]
    try:
        poc_schema._db_tool.execute_query(create_sql)
    except Exception as exc:  # noqa: BLE001 - surface exact SQL + DB error per file
        pytest.fail(
            f"db/poc/{sql_path} 無法建立 view：{type(exc).__name__}: {exc}\n\n"
            f"執行的 SQL：\n{create_sql}",
            pytrace=False,
        )
