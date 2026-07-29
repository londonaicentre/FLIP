#!/bin/bash
# Invoked by the postgres docker-entrypoint-initdb.d on first init. Reads the
# data analyst password from the environment and passes it to the SQL script
# via psql's -v variable mechanism, so it never appears in the image or in
# checked-in source.
set -euo pipefail

if [ -z "${DATA_ACCESS_POSTGRES_PASSWORD:-}" ]; then
    echo "ERROR: DATA_ACCESS_POSTGRES_PASSWORD must be set to create the data_analyst_reader role." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v "data_analyst_password=$DATA_ACCESS_POSTGRES_PASSWORD" \
     -f /flip/omop/create_readonly_users.sql
