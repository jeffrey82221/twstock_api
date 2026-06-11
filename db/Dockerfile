# Stage 1: Build the extension
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

# Stage 2: Final lightweight image
FROM postgres:16

# Install runtime dependency for curl
RUN apt-get update && apt-get install -y \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled extension files from builder stage
COPY --from=builder /usr/lib/postgresql/16/lib/http.so /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/http* /usr/share/postgresql/16/extension/