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
# Runs INSIDE the running xnat-db, piped in by xnat-db-drop-unused-dump (trust/xnat and
# trust/deploy/helm Makefiles) after the deploy (FLIP#1812). A migration dump still inside the live
# data dir means xnat-db stayed on the old major, because the deployed image is an older release.
# That database goes on changing, so the dump must not wait for a later start of the new image.
set -eu

dump="$PGDATA/xnat-migrate.dump"
[ -f "$dump" ] || exit 0
rm -f "$dump"
echo "⚠️  xnat-db still runs PostgreSQL $(cat "$PGDATA/PG_VERSION"): the deployed image is an older release. Removed the unused migration dump."
