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
# The newer image looks for the dump one level above its pg<major>/, so only a cluster at the
# volume root can be moved: from inside a pg<major>/ the dump would land where nothing reads it.
case $(basename "$PGDATA") in pg[0-9]*)
  >&2 echo "ERROR: xnat-db runs from $PGDATA, and moving on from a pg<major>/ data dir is not implemented."
  exit 1 ;;
esac
# A volume first initialised with POSTGRES_USER=xnat has no postgres role (FLIP#1666).
role=postgres
psql -XqAt -U postgres -d postgres -c 'select 1' >/dev/null 2>&1 || role=${XNAT_DATASOURCE_USERNAME:-xnat}

# Left by an earlier move this site rolled back from: this cluster has changed since. Moved aside,
# as the only copy of what was written on it, until xnat-db-remove-old-cluster deletes it.
[ ! -e "$PGDATA/pg$target" ] || mv "$PGDATA/pg$target" "$PGDATA/pg$target.replaced-$(date +%Y%m%d%H%M%S)"
tmp="$PGDATA/xnat-migrate.dump.tmp"
trap 'rm -f "$tmp"' EXIT
pg_dump -Fc -U "$role" -f "$tmp" "$POSTGRES_DB"
pg_restore -l "$tmp" >/dev/null
mv "$tmp" "$PGDATA/xnat-migrate.dump"
ls -l "$PGDATA/xnat-migrate.dump"
