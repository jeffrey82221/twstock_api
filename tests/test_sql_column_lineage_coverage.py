"""Check column-level lineage coverage for every `db/poc/*.sql` view that has
an upstream view.

`tests/test_sql_table_lineage_consistency.py` already cross-validates a
view's *table-level* upstream between `pg_tool.get_dependent_views` (live
database catalog) and `tools/render_lineagex.py` (static SQL text parse).
This test goes one level deeper: for a view that DOES have at least one
upstream view (per `get_dependent_views`), every one of its *columns* should
also have at least one upstream *column* in LineageX's column-level lineage
(`output.json`'s `columns` field). A column with an empty (or entirely
missing) direct-source list means LineageX's static parser could not trace
where that column's value comes from, even though the view as a whole
clearly has an upstream table -- almost always because the column
expression uses a SQL construct (nested JSON path operators, a function
call wrapping the reference, dynamic casting, ...) that LineageX 0.0.27
cannot statically resolve to a source column. Highlighting exactly which
columns are affected lets an AI or engineer go straight to the SQL and
adjust the expression until LineageX can trace it (or confirms the gap is
expected and documents why).

Crucially, the set of columns to check per view is read live from the
pg_server-backed catalog (`pg_tool.PostgreSQLTool.get_view_columns`,
`information_schema.columns` for the view actually built by pgserver), not
from whichever keys happen to exist in LineageX's own `output.json`. This
matters because it also catches a column LineageX drops from its output
entirely (not just one with an empty source list) -- a gap that iterating
`output.json`'s own keys would silently miss.

See `tests/conftest.py` for the shared pgserver-backed mock database and the
`sql_path` parametrization (every `db/poc/*.sql` file, in DAG order).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import pytest

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `tools/` is a plain directory (no package __init__), same import trick
# `tests/test_sql_table_lineage_consistency.py` uses.
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
    return json.loads((output_dir / "output.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def poc_schema_build_errors(pipeline) -> Dict[str, Optional[str]]:
    """Build every `db/poc/*.sql` view into a fresh `poc` schema, one by
    one, tolerating per-file failures (unlike `Pipeline.create_views()`,
    which aborts at the first error). See
    `tests/test_sql_table_lineage_consistency.py` for the identical helper
    this mirrors.
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
def pgtool_view_columns(
    pipeline, db_tool, poc_schema_build_errors
) -> Dict[str, List[str]]:
    """Ground-truth column names of every successfully-built `poc.*` view,
    read live from PostgreSQL's catalog via
    `pg_tool.PostgreSQLTool.get_view_columns` (`information_schema.columns`
    for the view as actually created by pgserver). This -- not whichever
    keys happen to exist in LineageX's own `output.json` -- is the set of
    columns this test checks lineage coverage against, so a column
    LineageX's static parser drops from its output entirely is caught the
    same way as one it reports with an empty source list.
    """
    columns: Dict[str, List[str]] = {}
    for sql_path in pipeline.ordered_sql_paths:
        if poc_schema_build_errors.get(sql_path) is not None:
            continue  # view doesn't exist in the catalog; can't be inspected
        view_name = _view_name(sql_path)
        columns[view_name] = db_tool.get_view_columns(f"poc.{view_name}")
    return columns


@pytest.fixture(scope="module")
def pgtool_upstream_map(
    pipeline, db_tool, poc_schema_build_errors
) -> Dict[str, Set[str]]:
    """Direct ("level 1") upstream map derived from
    `pg_tool.PostgreSQLTool.get_dependent_views`, built by inverting it once
    per successfully-built `poc` view. See
    `tests/test_sql_table_lineage_consistency.py` for the identical helper
    this mirrors -- used here only to decide whether a view has *any*
    upstream view at all, which gates the column-coverage check below.
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


def _columns_missing_upstream(
    actual_columns: List[str], lineage_columns: dict
) -> List[str]:
    """Names, from `actual_columns` (the view's real, pg_server-built
    column list), that have no traceable upstream column in LineageX's
    lineage output.

    `output.json`'s `columns` field maps `column_name -> [direct_sources,
    indirect_sources]`; `direct_sources` is the SELECT-level column lineage
    (what the column's value is derived from), `indirect_sources` is columns
    only involved in filter/join conditions. A column genuinely has "no
    traceable upstream column" when it is either missing from
    `lineage_columns` entirely (LineageX dropped it from its output) or
    present with an empty `direct_sources` list. Driving the loop from
    `actual_columns` -- the ground-truth catalog column list -- rather than
    from `lineage_columns.keys()` is what catches the former case; iterating
    LineageX's own keys would silently skip a column it never emitted at
    all.
    """
    lower_lineage_columns = {k.lower(): v for k, v in lineage_columns.items()}
    missing = []
    for column_name in actual_columns:
        sources = lineage_columns.get(column_name)
        if sources is None:
            # Resolve case-folding differences between the catalog (always
            # lower-case for unquoted identifiers) and LineageX's output.
            sources = lower_lineage_columns.get(column_name.lower())
        direct_sources = sources[0] if sources else []
        if not direct_sources:
            missing.append(column_name)
    return missing


def test_view_columns_have_upstream_column(
    poc_schema_build_errors,
    lineage_json,
    pgtool_upstream_map,
    pgtool_view_columns,
    sql_path,
):
    """Every column of `db/poc/{sql_path}` must have at least one upstream
    column in LineageX's lineage, provided the view itself has at least one
    upstream *view* (per `pg_tool.get_dependent_views`).

    The set of columns actually checked comes from `pgtool_view_columns` --
    i.e. `pg_tool.PostgreSQLTool.get_view_columns` querying the real view
    that pg_server just built (`information_schema.columns`) -- not from
    LineageX's own `output.json` keys. This keeps the check honest: a
    column LineageX's static parser drops from its output entirely is
    flagged exactly the same way as one it reports with an empty
    direct-source list.

    Views with no upstream view at all (pure base/leaf queries, e.g. reading
    only from a `raw_*` JSON column with no further `poc.*` reference, or an
    independent date-list generator) are out of scope for this check and are
    skipped -- there is nothing upstream for their columns to trace to.

    A failure lists every column missing a traceable upstream column, plus
    the view's known upstream table(s), so the gap can be investigated
    directly: check whether the column expression uses a construct (nested
    `->`/`->>` JSON paths, a wrapping function call, a `CASE`/computed
    value, a subquery) that LineageX 0.0.27's static parser cannot resolve
    to a source column, or whether the column is a genuine constant/derived
    value with no real upstream.
    """
    view_name = _view_name(sql_path)
    qualified = f"poc.{view_name}"

    build_error = poc_schema_build_errors.get(sql_path)
    if build_error is not None:
        pytest.skip(
            f"db/poc/{sql_path} 無法建立 view，略過欄位 lineage 覆蓋檢查"
            f"（view 建立失敗屬於 tests/test_sql_view_creation.py 的檢查範圍）："
            f"{build_error}"
        )

    upstream_views = pgtool_upstream_map.get(view_name, set())
    if not upstream_views:
        pytest.skip(
            f"db/poc/{sql_path}（{qualified}）沒有任何上游 view"
            "（get_dependent_views 反查結果為空），不屬於本測試的檢查範圍。"
        )

    lineage_entry = lineage_json.get(qualified)
    if lineage_entry is None:
        pytest.fail(
            f"db/poc/{sql_path}：LineageX 的 output.json 找不到 {qualified}"
            "，無法取得其欄位 lineage，請檢查此 SQL 是否有 LineageX 無法解析的語法。",
            pytrace=False,
        )

    actual_columns = pgtool_view_columns.get(view_name, [])
    missing_columns = _columns_missing_upstream(
        actual_columns, lineage_entry.get("columns", {})
    )
    if missing_columns:
        all_columns = sorted(actual_columns)
        pytest.fail(
            f"db/poc/{sql_path}（{qualified}）有欄位在 LineageX 的欄位 lineage 中"
            "找不到任何上游欄位，但此 view 依 pg_tool.get_dependent_views 確實"
            f"有上游 view：{sorted(upstream_views)}\n\n"
            f"缺少上游欄位的 column（共 {len(missing_columns)} 個，需要 AI/工程師"
            f"研究原因）：{sorted(missing_columns)}\n\n"
            f"此 view 在 pg_server 建好的實際欄位（來自 pg_tool.get_view_columns 查詢 "
            f"information_schema.columns，共 {len(all_columns)} 個）：{all_columns}\n\n"
            "請檢查 db/poc/{0} 中上述欄位的 SQL 運算式，常見原因包含："
            "巢狀 JSON 路徑運算子（如 col->'row'->>'key'）、外層包了函式呼叫"
            "（如 custom.parse_iso_date(...)）、CASE/子查詢等 LineageX 0.0.27"
            "靜態解析無法追蹤到來源欄位的寫法；也可能是此欄位本質上就是常數/"
            "計算值而沒有真正的上游欄位，若屬此情況請在 SQL 註解中說明。".format(
                sql_path
            ),
            pytrace=False,
        )
