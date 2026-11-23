"""Targeted pop updates (Pipeline.pop_definition_drift / update_mat_views).

Runs on the pgserver mock DB from tests/conftest.py, which has no pg_ivm, so
only the seed-table path is exercised here (hidden view + pop table). The
IMMV path (DROP + pgivm.create_immv) needs the real docker DB.
"""
from __future__ import annotations

import pytest

SEED = "chain_list"


@pytest.fixture(scope="module")
def p(pipeline):
    """poc built from the current SQL; empty pop/hidden schemas."""
    pipeline.create_views()
    for schema in ("pop", "hidden"):
        pipeline._db_tool.execute_query(f"DROP SCHEMA IF EXISTS {schema} CASCADE;")
        pipeline._db_tool.execute_query(f"CREATE SCHEMA {schema};")
    return pipeline


def _status(p, name):
    (row,) = p.pop_definition_drift([name])
    return row["status"]


def test_missing_seed_is_created_and_then_in_sync(p):
    assert _status(p, SEED) == "missing"

    plan = p.update_mat_views([SEED], dry_run=True)
    assert [(x["name"], x["action"]) for x in plan] == [(SEED, "create_seed_table+hidden_view")]
    assert _status(p, SEED) == "missing"  # dry run changed nothing

    (done,) = p.update_mat_views([SEED])
    assert done["result"] == "ok"
    assert _status(p, SEED) == "ok"
    assert p.update_mat_views([SEED]) == []  # nothing left to do


def test_changed_hidden_view_is_replaced_without_touching_seed_rows(p):
    p._db_tool.execute_query(f"INSERT INTO pop.{SEED} VALUES ('X000', 'kept row');")
    p._db_tool.execute_query(
        f"CREATE OR REPLACE VIEW hidden.{SEED} AS SELECT ic_code, ic_name FROM pop.{SEED}")
    assert _status(p, SEED) == "changed"

    (done,) = p.update_mat_views([SEED])
    assert (done["action"], done["result"]) == ("replace_hidden_view", "ok")
    assert _status(p, SEED) == "ok"
    assert p._db_tool.fetch_all(f"SELECT ic_name FROM pop.{SEED}") == [("kept row",)]


def test_seed_column_change_is_refused_and_data_kept(p):
    # A hidden view with a different column set cannot be CREATE OR REPLACEd
    # back; update_mat_views must report it instead of dropping pop data.
    p._db_tool.execute_query(f"DROP VIEW hidden.{SEED};")
    p._db_tool.execute_query(f"CREATE VIEW hidden.{SEED} AS SELECT ic_code, ic_name, 1 AS extra FROM pop.{SEED}")

    (done,) = p.update_mat_views([SEED])
    assert done["result"].startswith("error:")
    assert "rule 18" in done["result"]
    assert p._db_tool.fetch_all(f"SELECT count(*) FROM pop.{SEED}") == [(1,)]


def test_object_blocked_by_missing_upstream_is_created_in_the_same_call(p):
    # company_finmind_list reads pop.company_list; with neither present the
    # downstream one does not compile until company_list is created first.
    # company_list itself reads pop.chain_info_list (fed by an IMMV pgserver
    # cannot build), so stand it in with an empty table of the same shape.
    p._db_tool.execute_query("CREATE TABLE pop.chain_info_list (LIKE poc.chain_info_list);")
    names = ["company_list", "company_finmind_list"]
    statuses = {r["name"]: r["status"] for r in p.pop_definition_drift(names)}
    assert statuses == {"company_list": "missing", "company_finmind_list": "invalid_sql"}

    done = {r["name"]: r for r in p.update_mat_views(names)}
    assert done["company_list"]["result"] == "ok"
    assert done["company_finmind_list"]["action"] == "create_seed_table+hidden_view"
    assert done["company_finmind_list"]["result"] == "ok"
    assert all(r["status"] == "ok" for r in p.pop_definition_drift(names))


def test_unknown_name_is_rejected(p):
    with pytest.raises(ValueError):
        p.pop_definition_drift(["no_such_view"])
