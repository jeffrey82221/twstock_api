"""Write helpers for re-crawling pop seed rows (wrapped by server.py MCP tools).

Kept free of fastmcp so tests can import it on the CI requirements.

Re-crawl = delete the seed rows, insert them again (``crawl_insert_filtered``).
Two pg_ivm facts shape this module (measured on the docker DB, pg_ivm 1.17,
2026-11-24, in rolled-back transactions):

* Deleting a seed row re-runs each downstream IMMV's query -- including its
  ``custom.http_get_content`` calls -- on the deleted rows, then removes the
  IMMV rows that match that result column for column. If the API now answers
  differently than when the row was crawled (which is exactly why one
  re-crawls: e.g. ``2330 sbl-history?as_of=2015-08-05`` was a 404 then and is
  a 200 now), nothing matches: the seed row goes, its IMMV rows stay behind
  as orphans.
* IMMVs refuse direct DML ("cannot change materialized view"), so orphans
  could only be cleaned by a full ``pgivm.refresh_immv`` (every seed row
  re-fetched).

``delete_seed_rows`` therefore, in one transaction:

1. pins the responses: for every downstream ``raw_*`` IMMV it rewrites the
   raw view's ``custom.http_get_content(<url>)`` into ``<url>``, joins that to
   the stored raw row and writes the stored response into
   ``job_control.http_cache`` (NULL -> cached 404), so pg_ivm recomputes
   exactly what it stored;
2. deletes the seed rows;
3. checks every IMMV that maps 1:1 to the seed lost exactly the deleted rows,
   and rolls everything back otherwise;
4. removes the pinned cache entries, so the next insert fetches live.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from jinja2 import Template

DELETE_MAX_ROWS = 500
EVICT_MAX_ROWS = 5000
SAMPLE_ROWS = 20

_POC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "poc")
_HTTP_CALL = "custom.http_get_content("

_DOWNSTREAM_IMMVS_SQL = """
    SELECT DISTINCT c.relname
    FROM pg_trigger t
    JOIN pg_depend d ON d.classid = 'pg_trigger'::regclass AND d.objid = t.oid
                    AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> t.tgrelid
    JOIN pg_class c ON c.oid = d.refobjid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE t.tgrelid = %s::regclass AND t.tgname LIKE 'IVM\\_trigger\\_%%' AND n.nspname = 'pop'
    ORDER BY 1
"""


def downstream_immvs(cur, table: str) -> List[str]:
    """pop IMMVs maintained by pg_ivm triggers on ``pop.<table>``."""
    cur.execute(_DOWNSTREAM_IMMVS_SQL, (f'pop."{table}"',))
    return [r[0] for r in cur.fetchall()]


def poc_raw_sql(immv: str) -> Optional[str]:
    """The db/poc template behind a raw_* IMMV, or None for other IMMVs."""
    path = os.path.join(_POC_DIR, f"{immv}.sql")
    if not immv.startswith("raw_") or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def url_select_sql(raw_sql: str) -> Tuple[str, List[str]]:
    """Rewrite ``custom.http_get_content(<url>) AS col`` into ``(<url>) AS col``.

    Returns the rewritten SQL (it now selects each request URL instead of the
    response) and the response column names, in order. Raises ValueError if a
    call is not of that shape.
    """
    sql = re.sub(r"--[^\n]*", "", raw_sql)
    out: List[str] = []
    cols: List[str] = []
    pos = 0
    while (start := sql.find(_HTTP_CALL, pos)) >= 0:
        i, depth, in_str = start + len(_HTTP_CALL), 1, False
        while depth:
            if i >= len(sql):
                raise ValueError("unbalanced parentheses in custom.http_get_content(...)")
            ch = sql[i]
            if ch == "'":
                in_str = not in_str
            elif not in_str and ch == "(":
                depth += 1
            elif not in_str and ch == ")":
                depth -= 1
            i += 1
        alias = re.match(r"\s*AS\s+(\w+)", sql[i:], re.IGNORECASE)
        if not alias:
            raise ValueError("custom.http_get_content(...) must be followed by AS <column>")
        cols.append(alias.group(1))
        out.append(sql[pos:start])
        out.append("(" + sql[start + len(_HTTP_CALL):i - 1] + ")")
        pos = i
    out.append(sql[pos:])
    return "".join(out).strip().removesuffix(";"), cols


def _columns(cur, table: str) -> List[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'pop' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def _count(cur, table: str, clause: str) -> int:
    cur.execute(f'SELECT count(*) FROM pop."{table}" WHERE TRUE{clause}')
    return cur.fetchone()[0]


def _pin_responses(cur, table: str, clause: str, immv: str, template: str) -> List[str]:
    """Cache the stored responses of ``immv`` for the seed rows in scope; return their URLs."""
    sql, http_cols = url_select_sql(template)
    # The raw view reads {{ schema }}.<seed>; point it at a temp view that
    # holds only the rows being deleted.
    cur.execute(f'CREATE OR REPLACE TEMP VIEW "{table}" AS SELECT * FROM pop."{table}" WHERE TRUE{clause}')
    sql = Template(sql).render(schema="pg_temp")
    keys = [c for c in _columns(cur, immv) if c not in http_cols]
    on = " AND ".join(f'u."{k}" IS NOT DISTINCT FROM r."{k}"' for k in keys) or "TRUE"
    pinned: List[str] = []
    for col in http_cols:
        cur.execute(
            f"""
            INSERT INTO job_control.http_cache (url, status_code, content, fetched_at)
            SELECT DISTINCT ON (u."{col}") u."{col}",
                   CASE WHEN r."{col}" IS NULL THEN 404 ELSE 200 END, r."{col}", clock_timestamp()
            FROM ({sql}) u JOIN pop."{immv}" r ON {on}
            WHERE u."{col}" IS NOT NULL
            ON CONFLICT (url) DO UPDATE SET status_code = EXCLUDED.status_code,
                content = EXCLUDED.content, fetched_at = EXCLUDED.fetched_at
            RETURNING url
            """
        )
        pinned += [r[0] for r in cur.fetchall()]
    cur.execute(f'DROP VIEW pg_temp."{table}"')
    return pinned


def delete_seed_rows(
    conn,
    pipeline,
    table: str,
    filters: Dict[str, Any],
    dry_run: bool = True,
    timeout_seconds: int = 120,
    max_rows: int = DELETE_MAX_ROWS,
    pin_responses: bool = True,
    allow_unverified: bool = False,
    find_immvs: Callable[[Any, str], List[str]] = downstream_immvs,
    raw_sql_for: Callable[[str], Optional[str]] = poc_raw_sql,
) -> Dict[str, Any]:
    """Delete ``pop.<table>`` rows matching ``filters`` and verify the IMMVs followed.

    ``table`` must already be validated as a seed table by the caller;
    ``pipeline`` supplies the filter-clause builder shared with
    ``crawl_insert_filtered`` (column names are checked against the table).
    Returns a dict with ``result`` in {dry_run, nothing_to_delete, deleted,
    rolled_back}; raises ValueError for bad input. The caller owns and
    closes ``conn``.
    """
    if not filters:
        raise ValueError("filters must not be empty (refusing to delete a whole seed table).")
    seed_cols = pipeline._get_table_columns("pop", table)
    if not seed_cols:
        raise ValueError(f"pop.{table} does not exist.")
    clause = pipeline._build_seed_filter_clause(seed_cols, filters)

    result: Dict[str, Any] = {"table": table, "filters": filters, "dry_run": dry_run}
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = '{int(timeout_seconds)}s'")
            cur.execute("SET LOCAL lock_timeout = '30s'")
            # Keep cron inserts on this seed out until we commit, so the
            # before/after IMMV counts below only reflect our own delete.
            cur.execute(f'LOCK TABLE pop."{table}" IN SHARE ROW EXCLUSIVE MODE')

            matching = _count(cur, table, clause)
            result["matching_rows"] = matching
            if matching > max_rows:
                raise ValueError(
                    f"{matching} rows match, above the limit of {max_rows}; narrow the filters.")
            cur.execute(
                "SELECT COALESCE(json_agg(s), '[]'::json) FROM "
                f'(SELECT * FROM pop."{table}" WHERE TRUE{clause} LIMIT {SAMPLE_ROWS}) s')
            result["sample"] = cur.fetchone()[0]

            immvs = []
            for name in find_immvs(cur, table):
                immv_cols = _columns(cur, name)
                # Count only the slice the filters can address in this IMMV
                # (e.g. stk_code), on both sides, to keep the counts cheap.
                scope = {c: v for c, v in filters.items() if c in immv_cols}
                immv_clause = pipeline._build_seed_filter_clause(immv_cols, scope)
                seed_scope = _count(cur, table, pipeline._build_seed_filter_clause(seed_cols, scope))
                before = _count(cur, name, immv_clause)
                immvs.append({
                    "immv": name,
                    "counted_by": sorted(scope) or "all rows",
                    "rows_before": before,
                    # A raw_* IMMV holds one row per seed row; that equality is
                    # what lets us check the delete exactly.
                    "one_to_one": seed_scope > 0 and before == seed_scope,
                    "_clause": immv_clause,
                })
            result["downstream_immvs"] = immvs

            if matching == 0:
                conn.rollback()
                result["result"] = "nothing_to_delete"
                return _public(result)

            pinned: List[str] = []
            if pin_responses:
                for s in immvs:
                    template = raw_sql_for(s["immv"])
                    if template is not None:
                        urls = _pin_responses(cur, table, clause, s["immv"], template)
                        s["pinned_responses"] = len(urls)
                        pinned += urls
            result["pinned_responses"] = len(pinned)

            if dry_run:
                conn.rollback()
                result["result"] = "dry_run"
                return _public(result)

            cur.execute("SELECT clock_timestamp()")
            started = cur.fetchone()[0]
            t0 = time.perf_counter()
            cur.execute(f'DELETE FROM pop."{table}" WHERE TRUE{clause}')
            deleted = cur.rowcount
            result["delete_seconds"] = round(time.perf_counter() - t0, 2)
            cur.execute("SELECT count(*) FROM job_control.http_log WHERE started_at >= %s", (started,))
            result["http_requests"] = cur.fetchone()[0]
            result["deleted"] = deleted

            for s in immvs:
                s["rows_after"] = _count(cur, s["immv"], s["_clause"])
                s["removed"] = s["rows_before"] - s["rows_after"]
            orphaned = [s["immv"] for s in immvs if s["one_to_one"] and s["removed"] != deleted]
            verified = [s["immv"] for s in immvs if s["one_to_one"] and s["removed"] == deleted]

            if orphaned:
                conn.rollback()
                result["result"] = "rolled_back"
                result["reason"] = (
                    f"{orphaned} kept rows for the deleted seed rows: pg_ivm recomputed a response "
                    "that differs from the stored one, so the rows did not match (they would be "
                    "orphans). Check that every IMMV with a changed response has a raw_* view "
                    "whose response could be pinned (pinned_responses).")
                return _public(result)
            if immvs and not verified and not allow_unverified:
                conn.rollback()
                result["result"] = "rolled_back"
                result["reason"] = (
                    "No downstream IMMV maps 1:1 to the seed rows in scope, so the delete cannot "
                    "be verified. Re-run with allow_unverified=true only after checking the "
                    "IMMV counts by hand.")
                return _public(result)

            if pinned:
                # Unpin: the next insert of these rows must fetch live.
                cur.execute("DELETE FROM job_control.http_cache WHERE url = ANY(%s)", (pinned,))
            conn.commit()
            result["result"] = "deleted"
            result["verified_immvs"] = verified
            return _public(result)
    except Exception as e:
        conn.rollback()
        if "Failed to connect" in str(e):
            raise RuntimeError(
                "Delete aborted: pg_ivm re-runs each IMMV query (with its HTTP calls) for deleted "
                "rows, a response was not pinned, and the API is unreachable. Start the API on "
                f"port 5002 first. ({e})") from e
        raise


def _public(result: Dict[str, Any]) -> Dict[str, Any]:
    for s in result.get("downstream_immvs", []):
        s.pop("_clause", None)
    return result


def validate_url_like(url_like: str) -> str:
    """A LIKE pattern narrow enough to evict on purpose, never the whole cache."""
    pattern = (url_like or "").strip()
    if "/api/" not in pattern:
        raise ValueError("url_like must contain an '/api/' path segment, e.g. "
                         "'%/api/company/2330/sbl-history?as_of=2015-%'.")
    if not re.search(r"\d{4}", pattern):
        raise ValueError("url_like must contain a stock code or a date (4+ digits) "
                         "so it cannot match every cached URL of an endpoint.")
    return pattern


def evict_http_cache(conn, url_like: str, dry_run: bool = True,
                     max_rows: int = EVICT_MAX_ROWS) -> Dict[str, Any]:
    """Delete ``job_control.http_cache`` entries whose url matches ``url_like``."""
    pattern = validate_url_like(url_like)
    result: Dict[str, Any] = {"url_like": pattern, "dry_run": dry_run}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(sum(n), 0)::int, COALESCE(json_object_agg(status_code, n), '{}'::json) "
                "FROM (SELECT status_code, count(*) AS n FROM job_control.http_cache "
                "      WHERE url LIKE %s GROUP BY 1) s", (pattern,))
            matching, statuses = cur.fetchone()
            result.update(matching_entries=matching, status_codes=statuses)
            if matching > max_rows:
                raise ValueError(f"{matching} entries match, above the limit of {max_rows}; "
                                 "narrow url_like.")
            cur.execute("SELECT url FROM job_control.http_cache WHERE url LIKE %s ORDER BY url LIMIT %s",
                        (pattern, SAMPLE_ROWS))
            result["sample_urls"] = [r[0] for r in cur.fetchall()]
            if dry_run or matching == 0:
                conn.rollback()
                result["evicted"] = 0
                return result
            cur.execute("DELETE FROM job_control.http_cache WHERE url LIKE %s", (pattern,))
            result["evicted"] = cur.rowcount
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
