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
# Runs INSIDE the running xnat-db, piped in by xnat-db-remove-old-cluster (trust/xnat and
# trust/deploy/helm Makefiles). Lists, and with --delete removes, the older-major cluster an
# upgrade left beside this one for a rollback, with its migration dump (FLIP#1812).
set -eu

root=$(dirname "$PGDATA")
live=$(basename "$PGDATA")
if [ "$live" != "pg$PG_MAJOR" ] || [ ! -s "$PGDATA/PG_VERSION" ]; then
  >&2 echo "ERROR: xnat-db is not running from a pg$PG_MAJOR/ data dir ($PGDATA); nothing removed."
  exit 1
fi
if [ ! -s "$root/PG_VERSION" ]; then
  echo "No older PostgreSQL cluster beside $PGDATA."
  exit 0
fi

kb=$(find "$root" -mindepth 1 -maxdepth 1 ! -name "$live" ! -name lost+found -exec du -sk {} + | awk '{s += $1} END {print s}')
echo "The PostgreSQL $(cat "$root/PG_VERSION") cluster in $root ($((kb / 1024)) MB) is kept beside $live/ since the move to $PG_MAJOR."
if [ "${1:-}" != --delete ]; then
  echo "Nothing removed. Once XNAT is confirmed on PostgreSQL $PG_MAJOR, re-run with YES=1 to delete it."
  exit 0
fi
find "$root" -mindepth 1 -maxdepth 1 ! -name "$live" ! -name lost+found -exec rm -rf {} +
echo "Removed. A rollback to the previous release is no longer possible from this volume."
