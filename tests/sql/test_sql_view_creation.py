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


def test_sbl_history_drops_records_from_overlapping_window(pipeline):
    """I017: the backend window is [as_of - 30, as_of] but samples are a month
    apart, so a completion on the previous sample date shows up in both
    responses. The normalized view keeps a record only under the as_of whose
    (as_of - 1 month, as_of] range contains its completion date."""
    schema = "test_sbl_history_overlap"
    pipeline._db_tool.execute_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
    pipeline._db_tool.execute_query(f"CREATE SCHEMA {schema};")

    def resp(as_of: str, completions: list[str]) -> str:
        records = ",".join(
            '{"transaction_date": "2025-03-27", "transaction_type": "議借", "quantity_lots": 432, '
            f'"fee_rate_pct": 0.35, "completion_close_price": 100, "completion_date": "{c}", "lending_days": 1}}'
            for c in completions
        )
        return f'{{"stock_id": "2317", "as_of": "{as_of}", "data_date": "{completions[-1]}", "records": [{records}]}}'

    try:
        pipeline._db_tool.execute_query(
            f"CREATE TABLE {schema}.raw_sbl_history (stk_code TEXT, as_of DATE, sbl_history JSONB);"
        )
        # 2025-10-05's window starts 2025-09-05, the previous sample date.
        pipeline._db_tool.execute_query(
            f"INSERT INTO {schema}.raw_sbl_history VALUES "
            f"('2317', DATE '2025-09-05', '{resp('2025-09-05', ['2025-08-06', '2025-09-05'])}'), "
            f"('2317', DATE '2025-10-05', '{resp('2025-10-05', ['2025-09-05', '2025-09-06', '2025-10-03'])}');"
        )
        pipeline._db_tool.execute_query(
            pipeline._get_view_create_sql(schema, "sbl_history.sql", target_schema=schema)
        )
        rows = pipeline._db_tool.fetch_all(
            f"SELECT as_of, completion_date FROM {schema}.sbl_history ORDER BY as_of, completion_date;"
        )
        assert [(a.isoformat(), c.isoformat()) for a, c in rows] == [
            ("2025-09-05", "2025-08-06"),
            ("2025-09-05", "2025-09-05"),
            ("2025-10-05", "2025-09-06"),
            ("2025-10-05", "2025-10-03"),
        ]
    finally:
        pipeline._db_tool.execute_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")


def test_foreign_ownership_monthly_samples_respect_supported_date(pipeline):
    schema = "test_foreign_ownership_monthly_list"
    pipeline._db_tool.execute_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
    pipeline._db_tool.execute_query(f"CREATE SCHEMA {schema};")

    try:
        pipeline._db_tool.execute_query(
            f"CREATE TABLE {schema}.company_basic_info_list "
            "(stk_code TEXT, listing_date DATE, market TEXT);"
        )
        pipeline._db_tool.execute_query(
            f"INSERT INTO {schema}.company_basic_info_list "
            "VALUES ('TEST', DATE '2000-01-01', '上市'), ('OTC', DATE '2000-01-01', '上櫃');"
        )
        pipeline._db_tool.execute_query(
            pipeline._get_view_create_sql(
                schema,
                "foreign_ownership_monthly_list.sql",
                target_schema=schema,
            )
        )

        samples = pipeline._db_tool.fetch_all(
            f"SELECT month_start_date FROM {schema}.foreign_ownership_monthly_list "
            "WHERE stk_code = 'TEST' ORDER BY month_start_date LIMIT 2;"
        )

        assert [row[0].isoformat() for row in samples] == [
            "2004-02-11",
            "2004-03-05",
        ]
        # I006: OTC stocks use TPEx qfii, which starts 2007-05-02
        otc = pipeline._db_tool.fetch_all(
            f"SELECT min(month_start_date) FROM {schema}.foreign_ownership_monthly_list WHERE stk_code = 'OTC';"
        )
        assert otc[0][0].isoformat() == "2007-05-05"
    finally:
        pipeline._db_tool.execute_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
