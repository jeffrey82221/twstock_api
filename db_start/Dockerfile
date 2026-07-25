# Stage 1: Build extensions that are not packaged for Debian
FROM postgres:16 AS builder

# Install compilation tools and dependencies (libcurl)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    postgresql-server-dev-16 \
    libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Clone and compile pgsql-http
RUN git clone https://github.com/pramsey/pgsql-http.git /tmp/pgsql-http \
    && cd /tmp/pgsql-http \
    && make \
    && make install

# Clone and compile pg_ivm (Incremental View Maintenance)
RUN git clone https://github.com/sraoss/pg_ivm.git /tmp/pg_ivm \
    && cd /tmp/pg_ivm \
    && make \
    && make install

# Stage 2: Final lightweight image
FROM postgres:16

# Runtime dependencies:
#   libcurl4               -> for pgsql-http
#   postgresql-16-cron     -> pg_cron extension (apt-packaged, no need to compile)
RUN apt-get update && apt-get install -y \
    libcurl4 \
    postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled extension files from builder stage
COPY --from=builder /usr/lib/postgresql/16/lib/http.so /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/http* /usr/share/postgresql/16/extension/

# Copy pg_ivm extension files from builder stage
COPY --from=builder /usr/lib/postgresql/16/lib/pg_ivm.so /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/pg_ivm* /usr/share/postgresql/16/extension/

# pg_cron needs to be loaded via shared_preload_libraries and bound to a
# specific database. We append the required GUCs to postgresql.conf at
# first-boot via an init script. This script only runs when PGDATA is
# empty (standard docker-entrypoint-initdb.d behaviour) -- for existing
# volumes the operator must apply these settings manually.
COPY enable_pg_cron.sh /docker-entrypoint-initdb.d/00-enable-pg-cron.sh
RUN chmod +x /docker-entrypoint-initdb.d/00-enable-pg-cron.sh
