

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '60');

SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');

CREATE SCHEMA poc;

CREATE SCHEMA pop;

CREATE SCHEMA hidden;

-- Schema-scoped immutable helper functions (e.g. {schema}.http_get_content)
-- are now rendered from db/immutable_func.sql and installed by
-- pipeline.Pipeline.create_views() / create_mat_views() at runtime.
