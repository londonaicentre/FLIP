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
# Step 3 of the chart's pre-upgrade xnat-db dump hook (FLIP#2071), and the one that writes to the
# volume: dump the xnat database beside the running cluster, where the new image's initdb restores
# it on first start (trust/xnat/postgres/restore-xnat-dump.sh).
#
# Runs in the NEW release's xnat-db image as uid/gid 70, so $PG_MAJOR is the target major, $PGDATA
# is where the new cluster will go, and pg_dump is the newer one — which can dump the older server.
# That server is still up and is reached over the network ($PGHOST:$PGPORT, the `xnat-db` Service),
# because this hook has no `pods/exec` and must not need it.
#
# Sequencing, through $PLAN_DIR (an emptyDir shared with the sibling `stop-xnat-web` container):
#   plan        written by the init container: `dump` or `skip`
#   go          written by stop-xnat-web once no xnat-web pod is left
#   abort       written by stop-xnat-web when it could not stop xnat-web
#   dump-result written here: `ok` or `failed`, which is what stop-xnat-web waits for and what
#               decides whether it puts xnat-web back before the Job (and the upgrade) fails.
set -eu

plan_dir=${PLAN_DIR:-/plan}
root=$(dirname "$PGDATA")
target=$PG_MAJOR
host=${PGHOST:-xnat-db}
port=${PGPORT:-5432}
deadline=$(($(date +%s) + ${TIMEOUT_SECONDS:-1800}))

say() { echo "xnat-db migrate (dump): $*"; }

[ "$(cat "$plan_dir/plan")" = dump ] || {
  say "nothing to dump."
  exit 0
}

# xnat-web must be gone before the dump, or the snapshot is of a database still being written to.
while [ ! -f "$plan_dir/go" ]; do
  [ ! -f "$plan_dir/abort" ] || {
    >&2 echo "ERROR: xnat-web was not stopped, so no dump was taken. Nothing was changed."
    exit 1
  }
  [ "$(date +%s)" -lt "$deadline" ] || {
    >&2 echo "ERROR: timed out waiting for xnat-web to stop. No dump was taken."
    exit 1
  }
  sleep 2
done

# A volume first initialised with POSTGRES_USER=xnat has no `postgres` role, and its `xnat` role is
# the superuser with the datasource password the xnat-db postStart hook keeps in sync (FLIP#1666).
pick_role() {
  role=postgres
  pw=$POSTGRES_PASSWORD
  PGPASSWORD=$pw psql -XqAt -h "$host" -p "$port" -U "$role" -d postgres -c 'select 1' >/dev/null 2>&1 && return 0
  role=${XNAT_DATASOURCE_USERNAME:-xnat}
  pw=${XNAT_DATASOURCE_PASSWORD:-}
  PGPASSWORD=$pw psql -XqAt -h "$host" -p "$port" -U "$role" -d postgres -c 'select 1' >/dev/null 2>&1 && return 0
  >&2 echo "ERROR: neither 'postgres' nor '$role' could connect to $host:$port. No dump was taken."
  return 1
}

dump_database() {
  pick_role || return 1
  say "dumping $POSTGRES_DB from $host:$port as $role for PostgreSQL $target"
  # Left by an earlier move this site rolled back from: this cluster has changed since. Moved
  # aside, as the only copy of what was written on it, until xnat-db-remove-old-cluster deletes it.
  if [ -e "$root/pg$target" ]; then
    mv "$root/pg$target" "$root/pg$target.replaced-$(date +%Y%m%d%H%M%S)" || return 1
  fi
  tmp="$root/xnat-migrate.dump.tmp"
  rm -f "$tmp"
  PGPASSWORD=$pw pg_dump -Fc -h "$host" -p "$port" -U "$role" -f "$tmp" "$POSTGRES_DB" || {
    rm -f "$tmp"
    return 1
  }
  # A dump the new server cannot read is worse than no dump: it would start the restore and fail.
  pg_restore -l "$tmp" >/dev/null || {
    rm -f "$tmp"
    return 1
  }
  mv "$tmp" "$root/xnat-migrate.dump" || return 1
  ls -l "$root/xnat-migrate.dump"
}

if dump_database; then
  echo ok >"$plan_dir/dump-result"
  say "done. The new server restores it on its first start."
else
  echo failed >"$plan_dir/dump-result"
  >&2 echo "ERROR: the dump failed, so nothing is upgraded; xnat-web is started again on the old server."
  exit 1
fi
