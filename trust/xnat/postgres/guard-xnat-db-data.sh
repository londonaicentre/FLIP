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
# xnat-db's entrypoint: refuse a data dir it would mishandle, then hand over to the password guard.
#
# PGDATA is pg<major>/ under the volume (Dockerfile). A cluster from an older major sits one level
# up, and the upgrade verbs leave a dump of it there (trust/xnat/scripts/xnat_db_migrate_dump.sh)
# for the first start to restore (restore-xnat-dump.sh), which marks a finished restore in PGDATA.
# Without these checks the first would start XNAT as an empty site, the second on half a database.
set -e

root=$(dirname "$PGDATA")
dump="$root/xnat-migrate.dump"

fail() {
  >&2 printf 'ERROR: %s\n' "$@"
  exit 1
}

if [ -s "$PGDATA/PG_VERSION" ]; then
  if [ -f "$dump" ] && [ ! -f "$PGDATA/xnat-restored" ]; then
    fail "$PGDATA was created, but restoring $dump into it did not finish." \
         "Delete $PGDATA and restart xnat-db to restore again."
  fi
elif [ -s "$root/PG_VERSION" ] && [ ! -f "$dump" ]; then
  fail "$root holds a PostgreSQL $(cat "$root/PG_VERSION") database, which PostgreSQL $PG_MAJOR cannot open," \
       "and no dump of it to restore. Move the trust with make upgrade-trust (Compose) or" \
       "make upgrade-trust-k8s (Helm), which dump it first. To go back, pin XNAT_TAG to the previous release."
fi

exec guard-xnat-db-passwords.sh "$@"
