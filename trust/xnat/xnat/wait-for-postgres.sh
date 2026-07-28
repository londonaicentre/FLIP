#!/bin/sh
#
# Portions derived from the XNAT docker-compose project
# Copyright (c) 2020, Washington University School of Medicine
# Licensed under the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
#
# Modifications Copyright (c) 2026,
# Guy's and St Thomas' NHS Foundation Trust & King's College London

# Container entrypoint:
#   1. Refuse to start with weak/missing datasource credentials.
#   2. Render the runtime config file (xnat-conf.properties) using the values
#      injected at container start — the password is NEVER part of the image.
#   3. Block until the xnat-db Postgres instance accepts connections.
#   4. Exec the original command (catalina.sh).

set -e

cmd="$@"

if [ -z "$XNAT_DATASOURCE_USERNAME" ] || [ -z "$XNAT_DATASOURCE_PASSWORD" ]; then
  >&2 echo "ERROR: XNAT_DATASOURCE_USERNAME and XNAT_DATASOURCE_PASSWORD must be set."
  >&2 echo "       Run 'make generate-xnat-credentials' on the deployment host."
  exit 1
fi

case "$XNAT_DATASOURCE_PASSWORD" in
  xnat|password|admin|"<run-make-generate-xnat-credentials>"|"$XNAT_DATASOURCE_USERNAME")
    >&2 echo "ERROR: XNAT_DATASOURCE_PASSWORD is set to a weak default or placeholder value."
    >&2 echo "       Run 'make generate-xnat-credentials' to rotate."
    exit 1
    ;;
esac

if [ -x /usr/local/bin/make-xnat-config.sh ]; then
  /usr/local/bin/make-xnat-config.sh
fi

export PGPASSWORD="$XNAT_DATASOURCE_PASSWORD"

# Retry while Postgres is genuinely unreachable, but fail fast when it is up and
# rejecting us. Postgres applies XNAT_DATASOURCE_* only on the very first initdb
# of the xnat-db data volume, so a password rotated afterwards (e.g.
# 'make generate-xnat-credentials FORCE=1') mismatches what the database expects —
# without this check that mismatch is indistinguishable from a startup wait.
# Falls back to psql's historical default (dbname = username) where
# XNAT_DATASOURCE_NAME is not in the environment.
xnat_db="${XNAT_DATASOURCE_NAME:-$XNAT_DATASOURCE_USERNAME}"
while ! psql_err=$(psql -U "$XNAT_DATASOURCE_USERNAME" -h xnat-db -d "$xnat_db" -c '\q' 2>&1); do
  case "$psql_err" in
    *"password authentication failed"*|*"role \"$XNAT_DATASOURCE_USERNAME\" does not exist"*|*"database \"$xnat_db\" does not exist"*)
      >&2 echo "ERROR: Postgres is up but rejected the XNAT datasource credentials:"
      >&2 echo "       $psql_err"
      >&2 echo "       If the credentials were rotated after xnat-db's data volume was initialised,"
      >&2 echo "       the database still holds the old ones. Update it to match the kit file:"
      >&2 echo "         psql -U postgres -c \"ALTER ROLE \\\"$XNAT_DATASOURCE_USERNAME\\\" WITH PASSWORD '<kit value>'\""
      >&2 echo "       (run inside the xnat-db container; likewise ALTER ROLE postgres for"
      >&2 echo "       XNAT_DATASOURCE_ADMIN_PASSWORD)."
      exit 1
      ;;
  esac
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 1
done

unset PGPASSWORD

>&2 echo "Postgres is up - executing command \"$cmd\""
exec $cmd
