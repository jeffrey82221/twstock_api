

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

-- pg_cron must be created in the database pinned by cron.database_name in
-- postgresql.conf (see db/enable_pg_cron.sh). For this project that is
-- POSTGRES_DB (=app_db) as set in db/docker-compose.yaml.
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '1500');

SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');

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

-- HTTP fetch helper -- already used by raw_* views.
CREATE OR REPLACE FUNCTION custom.http_get_content(p_url text)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT content::JSONB FROM http_get(p_url)
$$;

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
