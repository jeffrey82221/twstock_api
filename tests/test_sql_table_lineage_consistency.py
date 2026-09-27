"""Cross-validate `db/poc/*.sql` upstream lineage between two independent
sources of truth:

1. `pg_tool.PostgreSQLTool.get_dependent_views` -- walks PostgreSQL's own
   catalog dependency graph (`pg_depend` -> `pg_rewrite` -> `pg_class`) on a
   *live* database that has every `db/poc/*.sql` view actually created.
   This function reports *downstream* dependents of a given view/table, so
   this test inverts it once (looping over every candidate `poc` view) to
   build a full "who is my direct upstream" map.
2. `tools/render_lineagex.build_lineage` -- runs LineageX's static SQL text
   parser (no live database involved) over the same 61 files and reports
   each view's direct ("level 1") upstream tables in its `output.json`.

If a SQL file's upstream set differs between the two methods, that is a
strong signal something in the SQL is written in a way one of the two
lineage tools cannot see correctly (e.g. a table reference LineageX's static
parser cannot resolve, or a stray/dead reference that only shows up in one
tool). Each mismatch is reported with the exact `db/poc/<file>.sql` path,
both upstream sets, and the symmetric set difference, so an AI or human can
go straight to the file and fix the SQL.

See `tests/conftest.py` for the shared pgserver-backed mock database and the
`sql_path` parametrization (every `db/poc/*.sql` file, in DAG order).
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Set

import pytest

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `tools/` is a plain directory (no package __init__), so make it importable
# the same way `tools/render_lineagex.py`'s own CLI usage expects.
_TOOLS_DIR = str(REPO_ROOT / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _view_name(sql_path: str) -> str:
    """Bare view name for a `db/poc/*.sql` filename, mirroring
    `pipeline.py`'s own `sql_path.split('.')[0]` convention."""
    return sql_path.split(".")[0]


def _strip_poc_prefix(table_name: str) -> str:
    return table_name[len("poc.") :] if table_name.startswith("poc.") else table_name


@pytest.fixture(scope="module")
def lineage_json(tmp_path_factory) -> dict:
    """Real `output.json` produced by `tools/render_lineagex.build_lineage`
    against the repository's actual `db/poc/*.sql` files (static SQL text
    parsing via LineageX -- no live database involved)."""
    render_lineagex = importlib.import_module("render_lineagex")
    output_dir = tmp_path_factory.mktemp("lineagex_output")
    render_lineagex.build_lineage(
        sql_dir=REPO_ROOT / "db" / "poc",
        output_dir=output_dir,
        schema="poc",
    )
    import json

    return json.loads((output_dir / "output.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def poc_schema_build_errors(pipeline) -> Dict[str, Optional[str]]:
    """Build every `db/poc/*.sql` view into a fresh `poc` schema, one by
    one, tolerating per-file failures (unlike `Pipeline.create_views()`,
    which aborts at the first error).

    Returns `{sql_path: None}` for a view that built successfully, or
    `{sql_path: "<error message>"}` for one that didn't -- a view that
    failed to build has no catalog entry, so it cannot be inspected by
    `pg_tool.get_dependent_views()` and is skipped (not failed) by the
    comparison test with a pointer back to `tests/test_sql_view_creation.py`,
    which already covers pure view-creation failures.
    """
    pipeline._db_tool.execute_query("DROP SCHEMA IF EXISTS poc CASCADE;")
    pipeline._db_tool.execute_query("CREATE SCHEMA poc;")

    errors: Dict[str, Optional[str]] = {}
    for sql_path in pipeline.ordered_sql_paths:
        create_sql = pipeline.view_create_sqls[sql_path]
        try:
            pipeline._db_tool.execute_query(create_sql)
            errors[sql_path] = None
        except Exception as exc:  # noqa: BLE001 - record and keep going
            errors[sql_path] = f"{type(exc).__name__}: {exc}"
    return errors


@pytest.fixture(scope="module")
def pgtool_upstream_map(
    pipeline, db_tool, poc_schema_build_errors
) -> Dict[str, Set[str]]:
    """Direct ("level 1") upstream map derived from
    `pg_tool.PostgreSQLTool.get_dependent_views`, built by inverting it once
    per successfully-built `poc` view.

    `get_dependent_views("poc.<T>")` lists views that *depend on* `T`; a
    dependent `D` at `level == 1` means `D` directly references `T`. Calling
    this once per candidate `T` (61 calls total) and collecting the `level
    == 1` hits gives, for every view `D`, the full set of views/tables it
    directly references -- i.e. `D`'s upstream set.
    """
    upstream: Dict[str, Set[str]] = {}
    for sql_path in pipeline.ordered_sql_paths:
        if poc_schema_build_errors.get(sql_path) is not None:
            continue  # view doesn't exist in the catalog; can't be inspected
        candidate = _view_name(sql_path)
        dependents = db_tool.get_dependent_views(f"poc.{candidate}")
        for dependent in dependents:
            if dependent["level"] != 1:
                continue
            dependent_view = _strip_poc_prefix(dependent["view"])
            upstream.setdefault(dependent_view, set()).add(candidate)
    return upstream


def test_view_lineage_upstream_matches_pgtool(
    poc_schema_build_errors, lineage_json, pgtool_upstream_map, sql_path
):
    """`db/poc/{sql_path}`'s direct upstream must agree between LineageX's
    static SQL parse and PostgreSQL's own catalog dependency graph (via
    `pg_tool.get_dependent_views`).

    A mismatch names the exact file, both upstream sets, and which tables
    are missing on which side, so the SQL can be inspected and fixed
    directly:

    - Tables only found by LineageX ("多算"): LineageX's static parser
      thinks the SQL references this table/view, but PostgreSQL's real
      dependency graph disagrees -- check for a reference LineageX
      mis-parsed (e.g. inside a string literal, an aliased subquery, or a
      comment `tools/render_lineagex.py`'s comment-stripping missed).
    - Tables only found by pg_tool ("漏算"): the view genuinely depends on
      this table/view in the live database, but LineageX's static parser
      could not see the reference in the SQL text -- check for a
      dynamically-built identifier, a construct LineageX 0.0.27 doesn't
      support (e.g. `LATERAL`, window functions over a renamed CTE, `USING`
      joins), or a Jinja-rendered fragment that only resolves at
      `CREATE VIEW` time.
    """
    view_name = _view_name(sql_path)
    qualified = f"poc.{view_name}"

    build_error = poc_schema_build_errors.get(sql_path)
    if build_error is not None:
        pytest.skip(
            f"db/poc/{sql_path} 無法建立 view，略過上游一致性比對"
            f"（view 建立失敗屬於 tests/test_sql_view_creation.py 的檢查範圍）："
            f"{build_error}"
        )

    lineage_entry = lineage_json.get(qualified)
    if lineage_entry is None:
        pytest.fail(
            f"db/poc/{sql_path}：LineageX 的 output.json 找不到 {qualified}"
            "，無法取得其上游，請檢查此 SQL 是否有 LineageX 無法解析的語法。",
            pytrace=False,
        )

    lineagex_upstream = {
        _strip_poc_prefix(table) for table in lineage_entry.get("tables", [])
    }
    pgtool_upstream = pgtool_upstream_map.get(view_name, set())

    only_in_lineagex = sorted(lineagex_upstream - pgtool_upstream)
    only_in_pgtool = sorted(pgtool_upstream - lineagex_upstream)

    if only_in_lineagex or only_in_pgtool:
        pytest.fail(
            f"db/poc/{sql_path} 的上游偵測不一致（view: {qualified}）\n\n"
            f"LineageX（靜態語法解析）判斷的上游：{sorted(lineagex_upstream) or '(無)'}\n"
            f"pg_tool.get_dependent_views（實際資料庫相依關係）判斷的上游："
            f"{sorted(pgtool_upstream) or '(無)'}\n\n"
            f"只在 LineageX 出現、pg_tool 沒有（可能是 LineageX 誤判/多算）："
            f"{only_in_lineagex or '(無)'}\n"
            f"只在 pg_tool 出現、LineageX 沒有（可能是 LineageX 漏算/解析失敗）："
            f"{only_in_pgtool or '(無)'}\n\n"
            f"請檢查 db/poc/{sql_path} 的 SQL 語法，修正後應使兩者判斷的上游一致。",
            pytrace=False,
        )
