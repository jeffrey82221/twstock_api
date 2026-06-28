

CREATE EXTENSION IF NOT EXISTS http;

CREATE EXTENSION IF NOT EXISTS pg_ivm;

-- pg_cron must be created in the database pinned by cron.database_name in
-- postgresql.conf (see db/enable_pg_cron.sh). For this project that is
-- POSTGRES_DB (=app_db) as set in db/docker-compose.yaml.
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '600');

SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');

CREATE SCHEMA poc;

CREATE SCHEMA pop;

CREATE SCHEMA hidden;

-- Schema-scoped immutable helper functions (e.g. {schema}.http_get_content)
-- are now rendered from db/immutable_func.sql and installed by
-- pipeline.Pipeline.create_views() / create_mat_views() at runtime.
