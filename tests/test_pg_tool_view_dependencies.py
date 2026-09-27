"""Unit tests for `pg_tool.PostgreSQLTool.get_dependent_views`.

We create our own small chain of dependent views inside a dedicated
`tests` Postgres schema (on the same `pgserver`-backed mock database used
by the other tests -- see `tests/conftest.py`) so this test suite is fully
self-contained and does not depend on the real `db/poc/*.sql` views.

Dependency chain built in `tests_schema`::

    tests.t1  (base table)
      -> tests.v1        (view,               depends only on t1)
           -> tests.v2   (view,               depends only on v1)
           -> tests.mv1  (materialized view,   depends only on v1)
                -> tests.v3   (view,           depends only on v2)

    tests.other_table  (unrelated base table)
      -> tests.v_other  (view, depends only on other_table)

`tests.other_table` / `tests.v_other` exist purely to confirm that
unrelated objects are never reported as dependents of `t1`'s chain.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def tests_schema(db_tool):
    """(Re)create the `tests` schema with a known view-dependency chain,
    once per test module."""
    db_tool.execute_query("DROP SCHEMA IF EXISTS tests CASCADE;")
    db_tool.execute_query("CREATE SCHEMA tests;")

    db_tool.execute_query(
        "CREATE TABLE tests.t1 (id int PRIMARY KEY, val text);"
    )
    db_tool.execute_query(
        "CREATE VIEW tests.v1 AS SELECT id, val FROM tests.t1;"
    )
    db_tool.execute_query(
        "CREATE VIEW tests.v2 AS SELECT id, val FROM tests.v1;"
    )
    db_tool.execute_query(
        "CREATE MATERIALIZED VIEW tests.mv1 AS SELECT id, val FROM tests.v1;"
    )
    db_tool.execute_query(
        "CREATE VIEW tests.v3 AS SELECT id, val FROM tests.v2;"
    )

    # Unrelated chain -- must never show up as a dependent of tests.t1's chain.
    db_tool.execute_query("CREATE TABLE tests.other_table (id int);")
    db_tool.execute_query(
        "CREATE VIEW tests.v_other AS SELECT id FROM tests.other_table;"
    )

    # Diamond dependency: v_diamond references t1 directly (level 1) AND
    # v1 (which is itself level 1 from t1, making v_diamond level 2 via
    # that path). The reported level must be the *max* of the two paths.
    db_tool.execute_query(
        "CREATE VIEW tests.v_diamond AS "
        "SELECT t1.id, v1.val FROM tests.t1 JOIN tests.v1 USING (id);"
    )

    return db_tool


def _by_view(dependents: list) -> dict:
    return {row["view"]: row for row in dependents}


def test_dependents_of_base_table(tests_schema):
    """`t1` is at the root of the chain: v1 (level 1), then v2 and mv1
    (level 2, both depend only on v1), then v3 (level 3, depends only on
    v2). `other_table`'s chain must not appear."""
    dependents = tests_schema.get_dependent_views("tests.t1")
    by_view = _by_view(dependents)

    assert by_view["tests.v1"]["level"] == 1
    assert by_view["tests.v1"]["is_materialized"] is False
    assert by_view["tests.v2"]["level"] == 2
    assert by_view["tests.mv1"]["level"] == 2
    assert by_view["tests.mv1"]["is_materialized"] is True
    assert by_view["tests.v3"]["level"] == 3

    assert by_view["tests.v_diamond"]["level"] == 2

    assert set(by_view) == {
        "tests.v1", "tests.v2", "tests.mv1", "tests.v3", "tests.v_diamond",
    }
    assert "tests.v_other" not in by_view


def test_diamond_dependency_reports_deepest_level(tests_schema):
    """`v_diamond` references `t1` both directly (level 1 via the JOIN on
    `tests.t1`) and indirectly through `v1` (level 2, since `v1` is itself
    level 1 from `t1`). The function must report the *deeper* of the two
    levels (2), matching the reference article's `GROUP BY ... max(level)`
    behaviour -- not the shallower direct-dependency level (1)."""
    dependents = tests_schema.get_dependent_views("tests.t1")
    by_view = _by_view(dependents)
    assert by_view["tests.v_diamond"]["level"] == 2


def test_dependents_of_intermediate_view(tests_schema):
    """`v1` sits in the middle of the chain: v2, mv1, and v_diamond all
    depend on it directly (level 1), v3 depends on it transitively
    through v2 (level 2)."""
    dependents = tests_schema.get_dependent_views("tests.v1")
    by_view = _by_view(dependents)

    assert set(by_view) == {
        "tests.v2", "tests.mv1", "tests.v3", "tests.v_diamond",
    }
    assert by_view["tests.v2"]["level"] == 1
    assert by_view["tests.mv1"]["level"] == 1
    assert by_view["tests.v_diamond"]["level"] == 1
    assert by_view["tests.v3"]["level"] == 2


def test_leaf_view_has_no_dependents(tests_schema):
    """`v3` is the end of the chain -- nothing depends on it."""
    assert tests_schema.get_dependent_views("tests.v3") == []


def test_materialized_view_has_no_dependents(tests_schema):
    """`mv1` is also a leaf in this chain."""
    assert tests_schema.get_dependent_views("tests.mv1") == []


def test_bare_name_uses_default_schema_argument(tests_schema):
    """Passing a bare view name plus `schema=` must match the fully
    schema-qualified call."""
    qualified = tests_schema.get_dependent_views("tests.v1")
    bare = tests_schema.get_dependent_views("v1", schema="tests")
    assert bare == qualified


def test_unrelated_view_is_isolated(tests_schema):
    """`v_other` only depends on `other_table`, which is unrelated to the
    `t1` chain -- confirms the two chains don't leak into each other."""
    dependents = tests_schema.get_dependent_views("tests.other_table")
    by_view = _by_view(dependents)
    assert set(by_view) == {"tests.v_other"}
    assert by_view["tests.v_other"]["level"] == 1
