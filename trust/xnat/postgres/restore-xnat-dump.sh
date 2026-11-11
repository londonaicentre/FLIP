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
# First initdb only: restore the xnat database from the dump the upgrade verbs took of the
# older-major cluster this one replaces (FLIP#1812). Runs after XNAT.sql has created the role and
# database, and before the server listens on TCP, so xnat-web never sees the empty database.
# guard-xnat-db-data.sh refuses a restart that finds the dump but no xnat-restored marker.
set -eu

dump="$(dirname "$PGDATA")/xnat-migrate.dump"
[ -f "$dump" ] || exit 0

echo "Restoring the xnat database from $dump"
pg_restore --exit-on-error --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$dump"
touch "$PGDATA/xnat-restored"
