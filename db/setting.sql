

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '6');

SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');

CREATE SCHEMA poc;
CREATE OR REPLACE FUNCTION poc.http_get_content(p_url text)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT content::JSONB FROM http_get(p_url)
$$;

CREATE SCHEMA pop;
CREATE OR REPLACE FUNCTION pop.http_get_content(p_url text)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT content::JSONB FROM http_get(p_url)
$$;

