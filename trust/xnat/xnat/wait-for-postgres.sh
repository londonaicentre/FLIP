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
  xnat|password|admin|"$XNAT_DATASOURCE_USERNAME")
    >&2 echo "ERROR: XNAT_DATASOURCE_PASSWORD is set to a weak default value."
    >&2 echo "       Run 'make generate-xnat-credentials' to rotate."
    exit 1
    ;;
esac

if [ -x /usr/local/bin/make-xnat-config.sh ]; then
  /usr/local/bin/make-xnat-config.sh
fi

export PGPASSWORD="$XNAT_DATASOURCE_PASSWORD"

until psql -U "$XNAT_DATASOURCE_USERNAME" -h xnat-db -c '\q'; do
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 1
done

unset PGPASSWORD

>&2 echo "Postgres is up - executing command \"$cmd\""
exec $cmd
