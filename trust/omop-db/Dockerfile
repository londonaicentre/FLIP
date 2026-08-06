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

# Builds the base omop postgres image: OMOP CDM 5.4 schema, indices and
# read-only roles only. The image is deliberately vocab-free — the licensed
# vocabulary bundle is streamed into a running database by
# /flip/omop/load_core_vocab.sh (one seeding step per environment; see
# README "Populating"), so every published artifact stays licence-clean and
# the image is CI-buildable. POSTGRES_* are runtime environment (set by the
# compose file) — initdb runs at first container start, not at build, so
# nothing credential-shaped is baked in.
FROM postgres:17

# The source label associates the GHCR package with this repo.
LABEL org.opencontainers.image.source="https://github.com/londonaicentre/FLIP" \
      org.opencontainers.image.description="FLIP trust OMOP database (OMOP CDM 5.4 + MI-CDM, schema and roles baked, vocabulary loaded at seed time; see trust/omop-db/)" \
      org.opencontainers.image.licenses="Apache-2.0"

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

# The vocabulary bundle ships as a zip and the postgres base has no extractor
# (it does carry tar and gzip). Installing unzip at build time rather than
# letting the Kubernetes vocab-load Job install it at runtime is what keeps that
# Job free of any dependency on a package mirror: a trust behind an egress
# allowlist can reach its S3 bucket but not necessarily Amazon Linux's repos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*

# Seed-time helpers, shipped in the image so any environment (dev host, EC2,
# k8s job) can run them with the image's own psql:
# - load_core_vocab.sh streams a vocabulary bundle into a RUNNING database
# - constraints.sql is applied AFTER data load (importing after constraints
#   fails), by the loader or `make apply-constraints`
COPY ./files/load_core_vocab.sh /flip/omop/load_core_vocab.sh
RUN chmod +x /flip/omop/load_core_vocab.sh
COPY ./files/OMOPCDM_postgresql_5.4_constraints.sql /flip/omop/constraints.sql

EXPOSE 5432
