#!/usr/bin/env bash
# Configure pg_cron at initdb time.
#
# This script runs once when PGDATA is first initialized (the standard
# /docker-entrypoint-initdb.d/*.sh hook). It must NOT contain SQL that
# requires pg_cron to already be loaded -- shared_preload_libraries is
# only honored after a full postgres restart, and the entrypoint will
# restart postgres for us at the end of initdb.
#
# What it does:
#   1. Append `shared_preload_libraries = 'pg_cron'` to postgresql.conf
#   2. Pin pg_cron to the application database (defaults to $POSTGRES_DB
#      if set, otherwise 'postgres').
#
# The actual `CREATE EXTENSION pg_cron;` is run separately from
# db/setting.sql against the target database, AFTER postgres has
# restarted with the preload in place.

set -euo pipefail

CRON_DB="${POSTGRES_DB:-postgres}"
PG_CONF="${PGDATA}/postgresql.conf"

echo "[enable_pg_cron] appending pg_cron settings to ${PG_CONF}"
{
    echo ""
    echo "# --- pg_cron (added by enable_pg_cron.sh) ---"
    echo "shared_preload_libraries = 'pg_cron'"
    echo "cron.database_name = '${CRON_DB}'"
} >> "${PG_CONF}"
