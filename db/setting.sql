

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

-- pg_cron must be created in the database pinned by cron.database_name in
-- postgresql.conf (see db/enable_pg_cron.sh). For this project that is
-- POSTGRES_DB (=app_db) as set in db/docker-compose.yaml.
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '15000');

SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');

SET work_mem = '512MB';

CREATE SCHEMA IF NOT EXISTS poc;

CREATE SCHEMA IF NOT EXISTS pop;

CREATE SCHEMA IF NOT EXISTS hidden;

-- Schema-scoped immutable helper functions (e.g. {schema}.http_get_content)
-- are now rendered from db/immutable_func.sql and installed by
-- pipeline.Pipeline.create_views() / create_mat_views() at runtime.
CREATE SCHEMA IF NOT EXISTS custom;
-- Immutable helper functions for schema {{ schema }}
-- This file is rendered as a Jinja template by pipeline.py; the
-- {{ schema }} placeholder is filled per-schema (e.g. poc, pop) and
-- executed before views / materialized views that reference these
-- functions are created.
--
-- Why wrap built-ins?
-- pg_ivm requires every expression in an incremental materialized view
-- to be IMMUTABLE. Several built-in date helpers are only STABLE
-- because their behavior depends on session GUCs (DateStyle, TimeZone):
--   - text::date              -> STABLE (DateStyle dependent)
--   - to_date(text, text)     -> STABLE (DateStyle dependent)
--   - date_trunc(text, ts)    -> STABLE (TimeZone dependent for ts)
-- We re-implement the equivalent operations using only IMMUTABLE
-- building blocks (make_date, substring, integer math) and mark the
-- wrapper IMMUTABLE so they can be safely used inside views and immvs.


-- Parse an ISO-8601 date string ('YYYY-MM-DD') into a date without
-- depending on DateStyle. Equivalent to:
--   text::date                 (when text matches 'YYYY-MM-DD')
--   to_date(text, 'YYYY-MM-DD')
-- but IMMUTABLE.
CREATE OR REPLACE FUNCTION custom.parse_iso_date(p_text text)
RETURNS date
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN p_text IS NULL OR length(p_text) < 10 THEN NULL
    ELSE make_date(
      substring(p_text FROM 1 FOR 4)::int,
      substring(p_text FROM 6 FOR 2)::int,
      substring(p_text FROM 9 FOR 2)::int
    )
  END
$$;

-- Truncate a date to the first day of its year. Equivalent to:
--   date_trunc('year', d)::date
-- but IMMUTABLE (extract(year from date) is IMMUTABLE; date_trunc on a
-- timestamp/timestamptz is only STABLE).
CREATE OR REPLACE FUNCTION custom.trunc_year(p_date date)
RETURNS date
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT make_date(EXTRACT(YEAR FROM p_date)::int, 1, 1)
$$;

-- Format a date as 'YYYY-MM-DD' text without depending on DateStyle.
-- Replaces implicit date->text conversion ('foo' || date_value), which
-- relies on date_out() (STABLE) and changes output under non-ISO
-- DateStyle settings.
CREATE OR REPLACE FUNCTION custom.date_to_iso(p_date date)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT lpad(EXTRACT(YEAR FROM p_date)::int::text, 4, '0') || '-' ||
         lpad(EXTRACT(MONTH FROM p_date)::int::text, 2, '0') || '-' ||
         lpad(EXTRACT(DAY FROM p_date)::int::text, 2, '0')
$$;

-- ============================================================
-- job_control: throttling + audit log for HTTP fetches
-- ============================================================
--
-- custom.http_get_content stays IMMUTABLE (callable from IVM
-- materialized views). For paths that need rate-limiting and an audit
-- trail, use custom.http_get_content_logged (VOLATILE).
--
-- Tables live in their own schema so DROP SCHEMA poc/pop CASCADE in
-- pipeline.py does not wipe them.

CREATE SCHEMA IF NOT EXISTS job_control;

-- Per-key configuration. Currently only 'fetch_sleep_ms' is consumed by
-- http_get_content_logged but the table is structured to hold future
-- throttling knobs without schema changes.
CREATE TABLE IF NOT EXISTS job_control.http_config (
    key   text PRIMARY KEY,
    value text NOT NULL
);

INSERT INTO job_control.http_config (key, value)
VALUES ('fetch_sleep_ms', '100')
ON CONFLICT (key) DO UPDATE
SET key = EXCLUDED.key,
    value = EXCLUDED.value;

-- Append-only audit log. One row per http_get_content_logged call.
CREATE TABLE IF NOT EXISTS job_control.http_log (
    id            bigserial PRIMARY KEY,
    url           text        NOT NULL,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    success       boolean     NOT NULL,
    status_code   int,            -- NULL when the call raised before getting a response (e.g. timeout)
    error_message text            -- NULL on success; SQLSTATE + message on failure
);

CREATE INDEX IF NOT EXISTS http_log_started_at_idx
    ON job_control.http_log (started_at);

-- VOLATILE wrapper around http_get with:
--   1. Configurable pre-call sleep (job_control.http_config['fetch_sleep_ms'])
--   2. Audit row written to job_control.http_log on both success and failure
--   3. Failure classification: timeout vs other (curl returns status 0 on
--      transport errors -- we surface SQLSTATE+message as error_message)
--
-- IMPORTANT: This function is VOLATILE. It MUST NOT be used inside an
-- incremental materialized view or any IMMUTABLE / STABLE context.
-- For IVM / immv use custom.http_get_content (the pure IMMUTABLE
-- version above).
CREATE OR REPLACE FUNCTION custom.http_get_content_logged(p_url text)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_sleep_ms   int;
    v_started_at timestamptz := clock_timestamp();
    v_finished_at timestamptz;
    v_status     int;
    v_content    text;
    v_result     jsonb;
BEGIN
    -- 1. Read configured sleep (ms). Treat missing / non-numeric as 0.
    SELECT COALESCE(NULLIF(value, '')::int, 0)
      INTO v_sleep_ms
      FROM job_control.http_config
     WHERE key = 'fetch_sleep_ms';

    IF v_sleep_ms IS NULL THEN
        v_sleep_ms := 0;
    END IF;

    IF v_sleep_ms > 0 THEN
        PERFORM pg_sleep(v_sleep_ms / 1000.0);
    END IF;

    -- 2. Perform the HTTP fetch. Wrap in a sub-block so we can catch
    --    any exception (timeout, DNS failure, connection refused, ...)
    --    and still write a log row.
    BEGIN
        SELECT status, content INTO v_status, v_content FROM http_get(p_url);
        v_finished_at := clock_timestamp();

        INSERT INTO job_control.http_log
            (url, started_at, finished_at, success, status_code, error_message)
        VALUES
            (p_url, v_started_at, v_finished_at,
             v_status BETWEEN 200 AND 299, v_status, NULL);

        IF v_status BETWEEN 200 AND 299 THEN
            v_result := v_content::jsonb;
        ELSE
            v_result := NULL;
        END IF;

        RETURN v_result;
    EXCEPTION WHEN OTHERS THEN
        v_finished_at := clock_timestamp();
        INSERT INTO job_control.http_log
            (url, started_at, finished_at, success, status_code, error_message)
        VALUES
            (p_url, v_started_at, v_finished_at,
             false, NULL, SQLSTATE || ': ' || SQLERRM);
        -- Return NULL so callers can decide whether to fail downstream.
        RETURN NULL;
    END;
END;
$$;

-- HTTP fetch helper -- already used by raw_* views.
CREATE OR REPLACE FUNCTION custom.http_get_content(p_url text)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT custom.http_get_content_logged(p_url)
$$;