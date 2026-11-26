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
# Step 1 of the chart's pre-upgrade xnat-db dump hook (FLIP#2071): decide, from the volume alone,
# whether this upgrade moves xnat-db to a newer PostgreSQL major. Runs as the hook's init
# container, in the NEW release's xnat-db image, so $PG_MAJOR is the major that is about to run
# and $PGDATA is where that image will keep its cluster (<volume root>/pg$PG_MAJOR).
#
# Writes one word to $PLAN_DIR/plan — `dump` or `skip` — which both of the hook's containers read.
# Refuses, rather than guessing, the one layout the restore path does not implement.
set -eu

plan_dir=${PLAN_DIR:-/plan}
root=$(dirname "$PGDATA")
target=$PG_MAJOR

say() { echo "xnat-db migrate (decide): $*"; }
plan() { echo "$1" >"$plan_dir/plan"; }

if [ -s "$PGDATA/PG_VERSION" ]; then
  # The move already happened (or this release does not change the major).
  say "$PGDATA already holds a PostgreSQL $(cat "$PGDATA/PG_VERSION") cluster: nothing to dump."
  plan skip
elif [ -s "$root/PG_VERSION" ]; then
  current=$(cat "$root/PG_VERSION")
  case "$current:$target" in *[!0-9:]* | :* | *:)
    >&2 echo "ERROR: cannot compare xnat-db's PostgreSQL major ('$current') with the image's ('$target'). Nothing was changed."
    exit 1
    ;;
  esac
  if [ "$current" -lt "$target" ]; then
    say "xnat-db moves from PostgreSQL $current to $target: xnat-web will be stopped and the xnat database dumped for the new server to restore."
    plan dump
  else
    say "the cluster in $root is already PostgreSQL $current: nothing to dump."
    plan skip
  fi
else
  # Only a cluster at the volume root can be moved: the new image looks for the dump one level
  # above its pg<major>/, so from inside a pg<major>/ the dump would land where nothing reads it.
  # Same refusal, in the same words, as the image's own guard (trust/xnat/postgres/guard-xnat-db-data.sh).
  for other in "$root"/pg*/PG_VERSION; do
    [ ! -s "$other" ] || {
      >&2 echo "ERROR: $(dirname "$other") holds a PostgreSQL $(cat "$other") database: moving on from a pg<major>/ data dir is not implemented. Pin xnat.image.pin to the release that image came from."
      exit 1
    }
  done
  say "$root holds no PostgreSQL cluster: nothing to dump."
  plan skip
fi
