"""Transaction-local memo in custom.http_get_content_logged (db/setting.sql).

pg_ivm evaluates IMMV maintenance queries under a fixed snapshot, so rows the
function writes to job_control.http_cache are invisible to later calls in the
same maintenance pass — the same URL used to be fetched 100+ times per tick.
The memo (a transaction-local custom GUC) must dedupe calls within one
transaction even when http_cache cannot help.

pgserver has neither the `http` nor the `pg_ivm` extension, so these tests
install a fake `public.http_get(text)` that records every call, and set
cache_ttl_seconds = 0 to model "http_cache is not visible".
"""
from __future__ import annotations

import psycopg
import pytest

URL_OK = "http://fake.local/ok"
URL_5XX = "http://fake.local/boom"


@pytest.fixture()
def conn(pg_dsn):
    setup = psycopg.connect(pg_dsn, autocommit=True)
    with setup.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS public.fake_http_calls (url text)")
        cur.execute("TRUNCATE public.fake_http_calls")
        cur.execute("""
            CREATE OR REPLACE FUNCTION public.http_get(p_url text)
            RETURNS TABLE(status int, content text)
            LANGUAGE plpgsql VOLATILE AS $$
            BEGIN
                INSERT INTO public.fake_http_calls VALUES (p_url);
                IF p_url LIKE '%boom%' THEN
                    RETURN QUERY SELECT 503, 'unavailable'::text;
                ELSE
                    RETURN QUERY SELECT 200, '{"found": true, "n": 1}'::text;
                END IF;
            END $$;
        """)
        cur.execute("SELECT value FROM job_control.http_config WHERE key = 'cache_ttl_seconds'")
        old_ttl = cur.fetchone()[0]
        cur.execute("UPDATE job_control.http_config SET value = '0' WHERE key = 'cache_ttl_seconds'")
    c = psycopg.connect(pg_dsn)  # explicit transactions
    try:
        yield c
    finally:
        c.rollback()
        c.close()
        with setup.cursor() as cur:
            cur.execute("UPDATE job_control.http_config SET value = %s WHERE key = 'cache_ttl_seconds'", (old_ttl,))
            cur.execute("DROP FUNCTION IF EXISTS public.http_get(text)")
            cur.execute("DROP TABLE IF EXISTS public.fake_http_calls")
        setup.close()


def _calls(conn, url):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.fake_http_calls WHERE url = %s", (url,))
        return cur.fetchone()[0]


def _fetch_n_times(conn, url, n):
    """Call the IMMUTABLE wrapper n times in one statement with a non-constant
    argument (so the planner cannot fold it into a single call)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT custom.http_get_content(u) FROM (SELECT %s || '' AS u FROM generate_series(1, %s)) s",
            (url, n),
        )
        return [r[0] for r in cur.fetchall()]


def test_repeats_in_one_transaction_hit_upstream_once(conn):
    results = _fetch_n_times(conn, URL_OK, 5)
    assert results == [{"found": True, "n": 1}] * 5
    results = _fetch_n_times(conn, URL_OK, 3)  # later statement, same transaction
    assert results == [{"found": True, "n": 1}] * 3
    assert _calls(conn, URL_OK) == 1


def test_memo_does_not_outlive_the_transaction(conn):
    _fetch_n_times(conn, URL_OK, 2)
    conn.commit()
    _fetch_n_times(conn, URL_OK, 2)  # new transaction: memo is gone, TTL=0 → refetch
    assert _calls(conn, URL_OK) == 2


def test_memo_is_discarded_on_rollback(conn):
    _fetch_n_times(conn, URL_OK, 2)
    conn.rollback()  # also rolls back the fake call log
    _fetch_n_times(conn, URL_OK, 2)
    assert _calls(conn, URL_OK) == 1


def test_5xx_is_memoised_only_within_the_transaction(conn):
    assert _fetch_n_times(conn, URL_5XX, 4) == [None] * 4
    assert _calls(conn, URL_5XX) == 1
    conn.commit()
    assert _fetch_n_times(conn, URL_5XX, 1) == [None]  # next tick retries
    assert _calls(conn, URL_5XX) == 2


def test_distinct_urls_are_memoised_separately(conn):
    _fetch_n_times(conn, URL_OK, 2)
    _fetch_n_times(conn, URL_OK + "?x=1", 2)
    assert _calls(conn, URL_OK) == 1
    assert _calls(conn, URL_OK + "?x=1") == 1
