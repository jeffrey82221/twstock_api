"""pop_maintenance: seed-row delete with response pinning + IMMV verification,
and http_cache eviction.

Runs on the pgserver mock DB (tests/conftest.py), which has no pg_ivm and no
http extension. pg_ivm's delete maintenance is emulated by an AFTER DELETE
trigger on the seed that does what pg_ivm does: recompute the response with
the real ``custom.http_get_content`` (served from job_control.http_cache
here) and delete only the IMMV rows that still match it column for column.
Every URL is pre-cached, so http_get (absent in pgserver) is never reached.
"""
from __future__ import annotations

import psycopg
import pytest

import pop_maintenance

SEED, RAW, NORM = "pm_seed", "pm_raw", "pm_norm"
URL = "'http://h:5002/api/x/' || {s} || '?as_of=' || {d}"

RAW_TEMPLATE = f"""
-- comment mentioning custom.http_get_content( must be ignored
SELECT
    stk_code,
    custom.http_get_content(
        ({URL.format(s="stk_code", d="month_start_date")})::TEXT
    ) AS payload,
    month_start_date AS as_of
FROM {{{{ schema }}}}.{SEED};
"""

_SETUP = f"""
CREATE SCHEMA IF NOT EXISTS pop;
DROP TABLE IF EXISTS pop.{SEED}, pop.{RAW}, pop.{NORM} CASCADE;
DELETE FROM job_control.http_cache;
CREATE TABLE pop.{SEED} (stk_code text, month_start_date date, PRIMARY KEY (stk_code, month_start_date));
CREATE TABLE pop.{RAW} (stk_code text, payload jsonb, as_of date);
CREATE TABLE pop.{NORM} (stk_code text, as_of date, i int);
INSERT INTO pop.{SEED} SELECT s, d::date FROM unnest(ARRAY['2330','2317']) s,
    generate_series(DATE '2015-01-05', DATE '2015-06-05', INTERVAL '1 month') d;
-- Stored at crawl time: one 404 (NULL), the rest {{"v": 1}}.
INSERT INTO pop.{RAW} SELECT stk_code,
    CASE WHEN stk_code = '2317' AND month_start_date = '2015-01-05' THEN NULL ELSE '{{"v": 1}}'::jsonb END,
    month_start_date FROM pop.{SEED};
INSERT INTO pop.{NORM} SELECT stk_code, month_start_date, i FROM pop.{SEED}, generate_series(1, 2) i;
-- What the API answers *now* (cached): a different response for every URL.
INSERT INTO job_control.http_cache (url, status_code, content, fetched_at)
SELECT {URL.format(s="stk_code", d="month_start_date")}, 200, '{{"v": 2}}', now() FROM pop.{SEED};
CREATE OR REPLACE FUNCTION pop.pm_emulate_ivm() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM pop.{RAW}
     WHERE stk_code = OLD.stk_code AND as_of = OLD.month_start_date
       AND payload IS NOT DISTINCT FROM
           custom.http_get_content(({URL.format(s="OLD.stk_code", d="OLD.month_start_date")})::TEXT);
    IF FOUND THEN
        DELETE FROM pop.{NORM} WHERE stk_code = OLD.stk_code AND as_of = OLD.month_start_date;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER pm_ivm AFTER DELETE ON pop.{SEED} FOR EACH ROW EXECUTE FUNCTION pop.pm_emulate_ivm();
"""


@pytest.fixture
def db(pipeline, pg_dsn):
    pipeline._db_tool.execute_query(_SETUP)
    conns = []

    def connect():
        conn = psycopg.connect(pg_dsn)
        conns.append(conn)
        return conn

    yield pipeline, connect
    for c in conns:
        c.close()
    pipeline._db_tool.execute_query(
        f"DROP TABLE IF EXISTS pop.{SEED}, pop.{RAW}, pop.{NORM} CASCADE; DELETE FROM job_control.http_cache;")


def _immvs(cur, table):
    return [RAW, NORM]


def _raw_sql(name):
    return RAW_TEMPLATE if name == RAW else None


def _counts(p):
    return tuple(p._db_tool.fetch_all(f"SELECT count(*) FROM pop.{t}")[0][0] for t in (SEED, RAW, NORM))


def _cache(p):
    return dict(p._db_tool.fetch_all("SELECT url, content::text FROM job_control.http_cache"))


def _delete(p, conn, filters, **kw):
    return pop_maintenance.delete_seed_rows(
        conn, p, SEED, filters, find_immvs=_immvs, raw_sql_for=_raw_sql, **kw)


TWO_2317 = {"stk_code": "2317", "month_start_date": ["2015-01-05", "2015-02-05"]}


def test_dry_run_reports_scope_and_changes_nothing(db):
    p, connect = db
    cache_before = _cache(p)
    out = _delete(p, connect(), TWO_2317)
    assert out["result"] == "dry_run"
    assert out["matching_rows"] == 2
    assert len(out["sample"]) == 2
    assert out["pinned_responses"] == 2
    by_name = {s["immv"]: s for s in out["downstream_immvs"]}
    # Counted on the stk_code slice only (the IMMVs have no month_start_date).
    assert by_name[RAW]["counted_by"] == ["stk_code"]
    assert by_name[RAW]["rows_before"] == 6 and by_name[RAW]["one_to_one"] is True
    assert by_name[NORM]["rows_before"] == 12 and by_name[NORM]["one_to_one"] is False
    assert _counts(p) == (12, 12, 24)
    assert _cache(p) == cache_before


def test_changed_response_without_pinning_is_rolled_back(db):
    # The real-DB failure mode: the API answers differently than at crawl
    # time, pg_ivm cannot match the stored rows, they would be orphaned.
    p, connect = db
    cache_before = _cache(p)
    out = _delete(p, connect(), TWO_2317, dry_run=False, pin_responses=False)
    assert out["result"] == "rolled_back"
    assert RAW in out["reason"]
    assert _counts(p) == (12, 12, 24)
    assert _cache(p) == cache_before


def test_pinning_deletes_cleanly_and_unpins(db):
    p, connect = db
    out = _delete(p, connect(), TWO_2317, dry_run=False)
    assert out["result"] == "deleted"
    assert out["deleted"] == 2
    assert out["pinned_responses"] == 2  # incl. the stored NULL, pinned as a cached 404
    assert out["http_requests"] == 0
    assert out["verified_immvs"] == [RAW]
    assert {s["immv"]: s["removed"] for s in out["downstream_immvs"]} == {RAW: 2, NORM: 4}
    assert _counts(p) == (10, 10, 20)
    cache = _cache(p)
    # Pinned URLs are gone so the re-crawl fetches live; others untouched.
    assert "http://h:5002/api/x/2317?as_of=2015-01-05" not in cache
    assert "http://h:5002/api/x/2317?as_of=2015-02-05" not in cache
    assert len(cache) == 10


def test_unverifiable_delete_is_rolled_back_unless_allowed(db):
    p, connect = db

    def only_fan_out(cur, table):
        return [NORM]

    out = pop_maintenance.delete_seed_rows(
        connect(), p, SEED, {"stk_code": "2330", "month_start_date": "2015-03-05"}, dry_run=False,
        find_immvs=only_fan_out, raw_sql_for=_raw_sql)
    assert out["result"] == "rolled_back"
    assert _counts(p) == (12, 12, 24)

    out = pop_maintenance.delete_seed_rows(
        connect(), p, SEED, {"stk_code": "2330", "month_start_date": "2015-03-05"}, dry_run=False,
        find_immvs=only_fan_out, raw_sql_for=_raw_sql, allow_unverified=True)
    assert out["result"] == "deleted" and out["deleted"] == 1


def test_no_match_deletes_nothing(db):
    p, connect = db
    out = _delete(p, connect(), {"stk_code": "9999"}, dry_run=False)
    assert out["result"] == "nothing_to_delete"
    assert out["matching_rows"] == 0


@pytest.mark.parametrize("filters", [{}, {"nope": "1"}, {"stk_code": []}, {"stk_code": None}])
def test_bad_filters_are_refused(db, filters):
    p, connect = db
    with pytest.raises(ValueError):
        _delete(p, connect(), filters, dry_run=False)
    assert _counts(p) == (12, 12, 24)


def test_row_limit_is_enforced(db):
    p, connect = db
    with pytest.raises(ValueError, match="above the limit"):
        _delete(p, connect(), {"stk_code": "2330"}, dry_run=False, max_rows=5)
    assert _counts(p) == (12, 12, 24)


@pytest.mark.parametrize("name, expected_cols", [
    ("raw_sbl_history", ["sbl_history"]),
    ("raw_company_info", ["basic", "business_items"]),
    ("raw_ohlcv_monthly", ["ohlcv"]),
])
def test_url_select_sql_on_real_raw_views(name, expected_cols):
    sql, cols = pop_maintenance.url_select_sql(pop_maintenance.poc_raw_sql(name))
    assert cols == expected_cols
    assert "http_get_content" not in sql
    assert "http://host.docker.internal:5002/api/" in sql


def test_every_raw_view_can_be_pinned():
    import glob
    import os

    for path in glob.glob(os.path.join(pop_maintenance._POC_DIR, "raw_*.sql")):
        name = os.path.basename(path)[:-4]
        _, cols = pop_maintenance.url_select_sql(pop_maintenance.poc_raw_sql(name))
        assert cols, name


def test_url_select_sql_rejects_unaliased_call():
    with pytest.raises(ValueError):
        pop_maintenance.url_select_sql("SELECT custom.http_get_content('u') FROM t")


@pytest.fixture
def cache(pipeline, pg_dsn):
    pipeline._db_tool.execute_query(
        "DELETE FROM job_control.http_cache;"
        "INSERT INTO job_control.http_cache (url, status_code, content, fetched_at) VALUES "
        "('http://h:5002/api/company/2330/sbl-history?as_of=2015-07-05', 404, NULL, now()),"
        "('http://h:5002/api/company/2330/sbl-history?as_of=2015-08-05', 404, NULL, now()),"
        "('http://h:5002/api/company/2330/sbl-history?as_of=2020-01-05', 200, '{}', now()),"
        "('http://h:5002/api/company/2317/sbl-history?as_of=2015-07-05', 200, '{}', now());")
    conn = psycopg.connect(pg_dsn)
    yield pipeline, conn
    conn.close()
    pipeline._db_tool.execute_query("DELETE FROM job_control.http_cache;")


def test_cache_evict_dry_run_then_delete(cache):
    p, conn = cache
    pattern = "%/api/company/2330/sbl-history?as_of=2015-%"
    out = pop_maintenance.evict_http_cache(conn, pattern)
    assert out["matching_entries"] == 2
    assert out["status_codes"] == {"404": 2}
    assert out["evicted"] == 0
    assert p._db_tool.fetch_all("SELECT count(*) FROM job_control.http_cache") == [(4,)]

    out = pop_maintenance.evict_http_cache(conn, pattern, dry_run=False)
    assert out["evicted"] == 2
    assert p._db_tool.fetch_all("SELECT url FROM job_control.http_cache ORDER BY url") == [
        ("http://h:5002/api/company/2317/sbl-history?as_of=2015-07-05",),
        ("http://h:5002/api/company/2330/sbl-history?as_of=2020-01-05",),
    ]


def test_cache_evict_limit(cache):
    p, conn = cache
    with pytest.raises(ValueError, match="above the limit"):
        pop_maintenance.evict_http_cache(conn, "%/api/company/%/sbl-history?as_of=2015-%", dry_run=False,
                                         max_rows=2)
    assert p._db_tool.fetch_all("SELECT count(*) FROM job_control.http_cache") == [(4,)]


@pytest.mark.parametrize("pattern", ["%", "%/api/%", "%/api/company/%/sbl-history%", "%2330%", ""])
def test_cache_evict_refuses_broad_patterns(pattern):
    with pytest.raises(ValueError):
        pop_maintenance.validate_url_like(pattern)
