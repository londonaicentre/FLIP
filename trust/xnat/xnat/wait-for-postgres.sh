#!/bin/sh
#
# Portions derived from the XNAT docker-compose project
# Copyright (c) 2020, Washington University School of Medicine
# Licensed under the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
#
# Modifications Copyright (c) 2026,
# Guy's and St Thomas' NHS Foundation Trust & King's College London

# wait-for-postgres.sh

set -e

cmd="$@"

until psql -U "$XNAT_DATASOURCE_USERNAME" -h xnat-db -c '\q'; do
  >&2 echo "Postgres is unavailable - sleeping"
  sleep 1
done

>&2 echo "Postgres is up - executing command \"$cmd\""
exec $cmd
