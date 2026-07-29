#!/bin/bash
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

# Invoked by the postgres docker-entrypoint-initdb.d on first init. Reads the
# data analyst password from the environment and passes it to the SQL script
# via psql's -v variable mechanism, so it never appears in the image or in
# checked-in source.
set -euo pipefail

if [ -z "${DATA_ACCESS_POSTGRES_PASSWORD:-}" ]; then
    echo "ERROR: DATA_ACCESS_POSTGRES_PASSWORD must be set to create the data_analyst_reader role." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v "data_analyst_password=$DATA_ACCESS_POSTGRES_PASSWORD" \
     -f /flip/omop/create_readonly_users.sql
