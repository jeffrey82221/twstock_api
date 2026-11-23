"""Which responses custom.http_get_content_logged writes to job_control.http_cache.

Regression: the "skip bodies with non-empty source_errors" guard evaluated to
NULL for responses without that key (and for 4xx, where the body is NULL), so
``IF ... AND NOT (NULL)`` skipped the cache write for almost every endpoint and
every IMMV rebuild re-fetched all of its URLs.

Uses the same fake ``public.http_get`` approach as test_http_memo.py, but with
cache_ttl_seconds > 0 so the cache-write branch runs.
"""
from __future__ import annotations

import psycopg
import pytest

BASE = "http://fake-cache.local/"
RESPONSES = {
    "plain": (200, '{"found": true}'),
    "not_found": (404, '{"detail": "no data"}'),
    "server_error": (503, "unavailable"),
    "not_json": (200, "<html>"),
    "empty_errors": (200, '{"found": true, "source_errors": []}'),
    "upstream_errors": (200, '{"found": false, "source_errors": [{"status": 402}]}'),
    "errors_object": (200, '{"found": true, "source_errors": {"x": 1}}'),
}


@pytest.fixture()
def conn(pg_dsn):
    setup = psycopg.connect(pg_dsn, autocommit=True)
    cases = " ".join(
        f"WHEN p_url = '{BASE}{k}' THEN RETURN QUERY SELECT {s}, $j${body}$j$::text;"
        for k, (s, body) in RESPONSES.items()
    )
    with setup.cursor() as cur:
        cur.execute(f"""
            CREATE OR REPLACE FUNCTION public.http_get(p_url text)
            RETURNS TABLE(status int, content text)
            LANGUAGE plpgsql VOLATILE AS $$
            BEGIN
                CASE {cases}
                ELSE RETURN QUERY SELECT 500, 'unexpected'::text;
                END CASE;
            END $$;
        """)
        cur.execute("DELETE FROM job_control.http_cache WHERE url LIKE %s", (BASE + "%",))
        cur.execute("SELECT value FROM job_control.http_config WHERE key = 'cache_ttl_seconds'")
        old_ttl = cur.fetchone()[0]
        cur.execute("UPDATE job_control.http_config SET value = '3600' WHERE key = 'cache_ttl_seconds'")
    c = psycopg.connect(pg_dsn, autocommit=True)
    try:
        yield c
    finally:
        c.close()
        with setup.cursor() as cur:
            cur.execute("DELETE FROM job_control.http_cache WHERE url LIKE %s", (BASE + "%",))
            cur.execute("UPDATE job_control.http_config SET value = %s WHERE key = 'cache_ttl_seconds'", (old_ttl,))
            cur.execute("DROP FUNCTION IF EXISTS public.http_get(text)")
        setup.close()


def _fetch_and_cached_status(conn, key):
    with conn.cursor() as cur:
        cur.execute("SELECT custom.http_get_content_logged(%s)", (BASE + key,))
        cur.execute("SELECT status_code FROM job_control.http_cache WHERE url = %s", (BASE + key,))
        row = cur.fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("key,expected", [
    ("plain", 200),            # no source_errors key: must be cached (the regression)
    ("not_found", 404),        # 4xx = "no data", cached
    ("empty_errors", 200),
    ("errors_object", 200),    # non-array source_errors: cached, and must not raise
    ("server_error", None),    # 5xx retried on the next call
    ("not_json", None),
    ("upstream_errors", None), # upstream failure wrapped in a 2xx
])
def test_cache_write_rules(conn, key, expected):
    assert _fetch_and_cached_status(conn, key) == expected
