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

# Builds the base omop postgres image. POSTGRES_USER / POSTGRES_PASSWORD /
# POSTGRES_DB are runtime environment (set by the compose file) — initdb runs at
# first container start, not at build, so nothing credential-shaped is baked in.
FROM postgres:17

# Copy initialization scripts (Schema DDL, Keys, Indices, Users)
COPY ./files/init.sql /docker-entrypoint-initdb.d/00_init.sql
COPY ./files/OMOPCDM_postgresql_5.4_ddl.sql /docker-entrypoint-initdb.d/10_ddl.sql
COPY ./files/OMOPCDM_postgresql_5.4_primary_keys.sql /docker-entrypoint-initdb.d/20_primary_keys.sql
COPY ./files/OMOPCDM_postgresql_5.4_indices.sql /docker-entrypoint-initdb.d/30_indices.sql
# The SQL is parameterized; a shell wrapper reads DATA_ACCESS_POSTGRES_PASSWORD
# from the runtime environment and invokes psql with -v.
COPY ./files/create_readonly_users.sql /flip/omop/create_readonly_users.sql
COPY ./files/create_readonly_users.sh /docker-entrypoint-initdb.d/50_create_readonly_users.sh
RUN chmod +x /docker-entrypoint-initdb.d/50_create_readonly_users.sh
COPY ./files/populate_vocabulary_tables.sql /docker-entrypoint-initdb.d/60_populate_vocabulary_tables.sql

# Copy vocabulary data. The bundle is not tracked in git (it contains licensed
# OMOP vocabularies — SNOMED CT, LOINC, etc. — that cannot be redistributed in
# this repository); fetch it with `make fetch-vocab-core` before building.
COPY ./data/vocab_aicentre_core_20240916 /flip/omop/vocab

# Copy constraints
# NOTE This needs to be applied after running the image and importing data -- if we import data after applying constraints, it will fail.
# See Makefile 'apply-constraints' command
COPY ./files/OMOPCDM_postgresql_5.4_constraints.sql /flip/omop/constraints.sql

EXPOSE 5432
