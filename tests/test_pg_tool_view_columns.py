"""Unit tests for `pg_tool.PostgreSQLTool.get_view_columns`.

We create our own small table/view inside a dedicated `tests_cols` Postgres
schema (on the same `pgserver`-backed mock database used by the other
tests -- see `tests/conftest.py`) so this test suite is fully self-contained
and does not depend on the real `db/poc/*.sql` views.

This method backs `tests/test_sql_column_lineage_coverage.py`, which uses it
to get the *real*, pg_server-built column list of a `poc.*` view (rather
than trusting whichever keys happen to exist in LineageX's own
`output.json`) before checking column-level lineage coverage.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def tests_cols_schema(db_tool):
    """(Re)create the `tests_cols` schema with a known table + view, once
    per test module."""
    db_tool.execute_query("DROP SCHEMA IF EXISTS tests_cols CASCADE;")
    db_tool.execute_query("CREATE SCHEMA tests_cols;")

    db_tool.execute_query(
        "CREATE TABLE tests_cols.t1 "
        "(id int PRIMARY KEY, name text, created_at date);"
    )
    db_tool.execute_query(
        "CREATE VIEW tests_cols.v1 AS "
        "SELECT id, name AS display_name, created_at FROM tests_cols.t1;"
    )
    # A view with zero columns worth of ordering ambiguity: single column.
    db_tool.execute_query(
        "CREATE VIEW tests_cols.v_single AS SELECT id FROM tests_cols.t1;"
    )

    return db_tool


def test_view_columns_in_declaration_order(tests_cols_schema):
    """`v1`'s SELECT list order (id, display_name, created_at) must be
    preserved -- `get_view_columns` orders by `ordinal_position`."""
    columns = tests_cols_schema.get_view_columns("tests_cols.v1")
    assert columns == ["id", "display_name", "created_at"]


def test_table_columns_in_declaration_order(tests_cols_schema):
    """Works on a base table too, not just a view."""
    columns = tests_cols_schema.get_view_columns("tests_cols.t1")
    assert columns == ["id", "name", "created_at"]


def test_single_column_view(tests_cols_schema):
    columns = tests_cols_schema.get_view_columns("tests_cols.v_single")
    assert columns == ["id"]


def test_bare_name_uses_default_schema_argument(tests_cols_schema):
    """Passing a bare view name plus `schema=` must match the fully
    schema-qualified call."""
    qualified = tests_cols_schema.get_view_columns("tests_cols.v1")
    bare = tests_cols_schema.get_view_columns("v1", schema="tests_cols")
    assert bare == qualified


def test_nonexistent_view_returns_empty_list(tests_cols_schema):
    """A view/table that doesn't exist yields no rows from
    `information_schema.columns`, not an error."""
    assert (
        tests_cols_schema.get_view_columns("tests_cols.does_not_exist") == []
    )
