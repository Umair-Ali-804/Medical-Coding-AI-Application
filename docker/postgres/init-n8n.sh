#!/bin/sh
# Creates a separate database/user for n8n on first Postgres start.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE USER ${N8N_DB_USER} WITH PASSWORD '${N8N_DB_PASSWORD}';
CREATE DATABASE ${N8N_DB_NAME} OWNER ${N8N_DB_USER};
SQL
