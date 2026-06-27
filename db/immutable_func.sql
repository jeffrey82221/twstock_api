-- Immutable helper functions for schema {{ schema }}
-- This file is rendered as a Jinja template by pipeline.py; the
-- {{ schema }} placeholder is filled per-schema (e.g. poc, pop) and
-- executed before views / materialized views that reference these
-- functions are created.

CREATE OR REPLACE FUNCTION {{ schema }}.http_get_content(p_url text)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT content::JSONB FROM http_get(p_url)
$$;
