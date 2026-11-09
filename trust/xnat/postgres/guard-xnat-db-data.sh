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

# The upgrade verbs need the old server running to dump it, so going back comes first.
# On Kubernetes the pod is deleted by hand: a StatefulSet does not replace a pod that is not Ready.
how="Pin XNAT_TAG (Helm: xnat.image.pin) to the previous release so xnat-db starts on it again (on Kubernetes, redeploy and then delete the xnat-db pod: a crash-looping one is not replaced). Then remove the pin and move the trust with make upgrade-trust (Compose) or make upgrade-trust-k8s (Helm), which dump it first."

# The old server writes postmaster.opts at every start: newer than $1, it has run since $1 was made.
old_started_since() { [ -e "$1" ] && [ "$root/postmaster.opts" -nt "$1" ]; }

if [ -s "$PGDATA/PG_VERSION" ]; then
  if [ -f "$dump" ] && [ ! -f "$PGDATA/xnat-restored" ]; then
    fail "$PGDATA was created, but restoring $dump into it did not finish." \
         "Delete $PGDATA and restart xnat-db to restore again."
  fi
  if old_started_since "$PGDATA/xnat-restored"; then
    fail "The older database in $root has run again since $PGDATA was restored from it (a rollback), so $PGDATA is out of date." "$how"
  fi
elif [ -s "$root/PG_VERSION" ]; then
  old="$root holds a PostgreSQL $(cat "$root/PG_VERSION") database, which PostgreSQL $PG_MAJOR cannot open,"
  [ -f "$dump" ] || fail "$old and no dump of it to restore." "$how"
  if old_started_since "$dump"; then
    fail "$old and it has run again since $dump was taken, so restoring that would lose what changed." "$how"
  fi
fi

exec guard-xnat-db-passwords.sh "$@"
