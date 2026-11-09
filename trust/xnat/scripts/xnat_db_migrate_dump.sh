#!/bin/sh
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Runs INSIDE the running xnat-db, piped in by xnat-db-migrate-dump (trust/xnat and
# trust/deploy/helm Makefiles) once xnat-web is stopped (FLIP#1812). Dumps the xnat database
# beside this cluster, where an image of the newer major $1 restores it on first start.
# Not baked into the image: it has to run in the old one.
set -eu

target=$1
# A volume first initialised with POSTGRES_USER=xnat has no postgres role (FLIP#1666).
role=postgres
psql -XqAt -U postgres -d postgres -c 'select 1' >/dev/null 2>&1 || role=${XNAT_DATASOURCE_USERNAME:-xnat}

# Left by an earlier move this site rolled back from: this cluster has changed since.
rm -rf "${PGDATA:?}/pg$target"
pg_dump -Fc -U "$role" -f "$PGDATA/xnat-migrate.dump.tmp" "$POSTGRES_DB"
pg_restore -l "$PGDATA/xnat-migrate.dump.tmp" >/dev/null
mv "$PGDATA/xnat-migrate.dump.tmp" "$PGDATA/xnat-migrate.dump"
ls -l "$PGDATA/xnat-migrate.dump"
