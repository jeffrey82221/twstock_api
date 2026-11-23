

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

-- pg_cron must be created in the database pinned by cron.database_name in
-- postgresql.conf (see db/enable_pg_cron.sh). For this project that is
-- POSTGRES_DB (=app_db) as set in db/docker-compose.yaml.
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- HTTP timeouts are set per DATABASE, not per session: http_set_curlopt()
-- only affects the session that runs this file, so every other connection
-- (pg_cron workers, pipeline probes, MCP) fell back to pgsql-http's 5s
-- default and turned slow-but-healthy API calls into NULLs. Keep this above
-- the API's 30s upstream timeout so it can return its handled timeout response.
-- Applies to sessions opened after this runs.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET http.timeout_msec = 45000', current_database());
    EXECUTE format('ALTER DATABASE %I SET http.curlopt_connecttimeout_ms = 15000', current_database());
END
$$;
SET http.timeout_msec = 45000;

SET work_mem = '1GB';

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

-- Last day of the month containing p_date. Equivalent to:
--   (p_date + INTERVAL '1 month' - INTERVAL '1 day')::date
-- but expressed with IMMUTABLE building blocks only (make_date + date - int),
-- so non-_list poc SQL can use it without a ::DATE cast (db/poc rule 1 / 3).
CREATE OR REPLACE FUNCTION custom.month_end(p_date date)
RETURNS date
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN EXTRACT(MONTH FROM p_date)::int = 12
      THEN make_date(EXTRACT(YEAR FROM p_date)::int + 1, 1, 1)
    ELSE make_date(EXTRACT(YEAR FROM p_date)::int, EXTRACT(MONTH FROM p_date)::int + 1, 1)
  END - 1
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
VALUES ('fetch_sleep_ms', '1')
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

-- Response cache keyed by URL.
--
-- Why: pg_ivm inlines the IMMUTABLE http_get_content() into every
-- expression that reads its result, so one row of a parsed view used to
-- fire the same request once per column (22x for market_valuation_summary,
-- 55x in the sbl_history plan), and every IMMV built on the same seed
-- table re-fetched the same URL. pg_ivm rejects IMMVs defined on other
-- IMMVs, so the duplication cannot be removed in SQL; the cache makes the
-- repeats free instead. NOTE: this table only dedupes ACROSS transactions —
-- inside one IVM maintenance pass its new rows are invisible (fixed
-- snapshot), so in-transaction repeats are handled by the transaction-local
-- memo in custom.http_get_content_logged (step 0).
--
-- 2xx and 4xx responses are cached (4xx here means "no data for this
-- date/stock", which is stable); 5xx and transport errors are not, so they
-- are retried on the next tick.
CREATE UNLOGGED TABLE IF NOT EXISTS job_control.http_cache (
    url         text PRIMARY KEY,
    status_code int         NOT NULL,
    content     jsonb,
    fetched_at  timestamptz NOT NULL
);

INSERT INTO job_control.http_config (key, value)
VALUES ('cache_ttl_seconds', '3600')
ON CONFLICT (key) DO NOTHING;

-- VOLATILE worker behind custom.http_get_content, with:
--   1. URL cache (job_control.http_cache, TTL http_config['cache_ttl_seconds'])
--   2. Configurable pre-call sleep (http_config['fetch_sleep_ms']), only on
--      cache misses
--   3. Audit row in job_control.http_log for every real request
--
-- It runs inside pg_ivm maintenance (through the IMMUTABLE wrapper), so it
-- must NOT use an EXCEPTION block: catching an error rolls back a
-- subtransaction, which corrupts pg_ivm's delta tuplestore ("invalid
-- tuplestore state") and can segfault the backend. A transport error
-- (timeout, connection refused) therefore propagates and aborts the
-- statement cleanly; it shows up in cron.job_run_details and the row is
-- retried on the next run.
--
-- Transaction-local memo (step 0): pg_ivm evaluates the maintenance query
-- under a fixed snapshot, so rows this function INSERTs into
-- job_control.http_cache are NOT visible to later calls in the same IVM
-- maintenance pass. Measured on 2026-11-23: one company_list tick fetched
-- the same /business-items URL 140+ times within ~20 s. The memo keeps each
-- response in a transaction-local custom GUC (set_config(..., is_local =>
-- true)), which is not subject to MVCC visibility and vanishes at
-- COMMIT/ROLLBACK; job_control.http_cache still covers repeats across
-- transactions. Encoding: 'j:<json>' = 2xx JSON, 'n:' = no usable body
-- (4xx / 5xx / non-JSON). A 5xx is memoised only for the rest of the
-- current transaction, so the next tick still retries it.
CREATE OR REPLACE FUNCTION custom.http_get_content_logged(p_url text)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_sleep_ms    int;
    v_ttl_s       int;
    v_started_at  timestamptz;
    v_status      int;
    v_content     text;
    v_cached      job_control.http_cache%ROWTYPE;
    v_json        jsonb;
    v_memo_key    text := 'twstock_http.u' || md5(p_url);
    v_memo        text;
BEGIN
    -- 0. Transaction-local memo (see header comment). An unset placeholder
    --    reads as NULL, and as '' once it has been reset at transaction end.
    v_memo := current_setting(v_memo_key, true);
    IF v_memo IS NOT NULL AND v_memo <> '' THEN
        RETURN CASE WHEN left(v_memo, 2) = 'j:' THEN substr(v_memo, 3)::jsonb END;
    END IF;

    -- 1. Cache lookup. Non-numeric config values fall back to defaults
    --    without raising (no EXCEPTION blocks allowed here, see above).
    SELECT CASE WHEN value ~ '^\d+$' THEN value::int ELSE 3600 END
      INTO v_ttl_s
      FROM job_control.http_config WHERE key = 'cache_ttl_seconds';
    v_ttl_s := COALESCE(v_ttl_s, 3600);

    IF v_ttl_s > 0 THEN
        SELECT * INTO v_cached FROM job_control.http_cache
         WHERE url = p_url
           AND fetched_at > clock_timestamp() - make_interval(secs => v_ttl_s);
        IF FOUND THEN
            v_json := CASE WHEN v_cached.status_code BETWEEN 200 AND 299
                           THEN v_cached.content END;
            PERFORM set_config(v_memo_key,
                               CASE WHEN v_json IS NULL THEN 'n:' ELSE 'j:' || v_json::text END,
                               true);
            RETURN v_json;
        END IF;
    END IF;

    -- 2. Throttle real requests only.
    SELECT CASE WHEN value ~ '^\d+$' THEN value::int ELSE 0 END
      INTO v_sleep_ms
      FROM job_control.http_config WHERE key = 'fetch_sleep_ms';
    IF COALESCE(v_sleep_ms, 0) > 0 THEN
        PERFORM pg_sleep(v_sleep_ms / 1000.0);
    END IF;

    -- 3. Fetch. Transport errors propagate (see header comment).
    v_started_at := clock_timestamp();
    SELECT status, content INTO v_status, v_content FROM http_get(p_url);

    IF v_status BETWEEN 200 AND 299 AND pg_input_is_valid(v_content, 'jsonb') THEN
        v_json := v_content::jsonb;
    END IF;

    INSERT INTO job_control.http_log
        (url, started_at, finished_at, success, status_code, error_message)
    VALUES
        (p_url, v_started_at, clock_timestamp(),
         v_json IS NOT NULL, v_status,
         CASE WHEN v_status BETWEEN 200 AND 299 AND v_json IS NULL
              THEN 'response body is not valid JSON' END);

    -- 4. Cache 2xx (valid JSON) and 4xx; skip 5xx so it is retried.
    --    Also skip 2xx bodies that report upstream failures (non-empty
    --    "source_errors", e.g. FinMind 402 "Requests reach the upper limit"
    --    wrapped as found=false): caching them would hand the failure to
    --    every retry within the TTL.
    IF v_ttl_s > 0
       AND (v_json IS NOT NULL OR v_status BETWEEN 400 AND 499)
       AND NOT (jsonb_typeof(v_json -> 'source_errors') = 'array'
                AND jsonb_array_length(v_json -> 'source_errors') > 0) THEN
        INSERT INTO job_control.http_cache (url, status_code, content, fetched_at)
        VALUES (p_url, v_status, v_json, clock_timestamp())
        ON CONFLICT (url) DO UPDATE
           SET status_code = EXCLUDED.status_code,
               content     = EXCLUDED.content,
               fetched_at  = EXCLUDED.fetched_at;
    END IF;

    PERFORM set_config(v_memo_key,
                       CASE WHEN v_json IS NULL THEN 'n:' ELSE 'j:' || v_json::text END,
                       true);
    RETURN v_json;
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


CREATE OR REPLACE FUNCTION custom.jsonb_array_elements(data jsonb)
RETURNS SETOF jsonb 
LANGUAGE sql 
IMMUTABLE 
PARALLEL SAFE AS 
$$
    SELECT jsonb_array_elements(data);
$$;


-- MCP Service Setup
DO $$
BEGIN
    CREATE ROLE mcp_reader WITH LOGIN PASSWORD 'password@MCP';
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'Role "mcp_reader" already exists, skipping.';
END
$$;

GRANT CONNECT ON DATABASE app_db TO mcp_reader;
GRANT USAGE ON SCHEMA public TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO mcp_reader;

GRANT USAGE ON SCHEMA pop TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA pop TO mcp_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA pop
GRANT SELECT ON TABLES TO mcp_reader;

GRANT USAGE ON SCHEMA poc TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA poc TO mcp_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA poc
GRANT SELECT ON TABLES TO mcp_reader;


-- 1. Allow the user to see and access objects inside the schema
GRANT USAGE ON SCHEMA custom TO mcp_reader;

GRANT EXECUTE ON ALL ROUTINES IN SCHEMA custom TO mcp_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA custom 
GRANT EXECUTE ON ROUTINES TO mcp_reader;

-- 以 postgres/admin 身份執行
CREATE or REPLACE VIEW public.job_run_details
  WITH (security_barrier = false)
AS
SELECT * FROM cron.job_run_details;

ALTER VIEW public.job_run_details OWNER TO postgres;

GRANT SELECT ON public.job_run_details TO mcp_reader;

-- 以 postgres/admin 身份執行
CREATE or REPLACE VIEW public.job
  WITH (security_barrier = false)
AS
SELECT * FROM cron.job;

ALTER VIEW public.job OWNER TO postgres;

GRANT SELECT ON public.job TO mcp_reader;