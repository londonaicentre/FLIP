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

# Streams the licensed core vocabulary bundle into a RUNNING OMOP database over
# an ordinary client connection (COPY FROM STDIN — no server-side file access,
# no image layer, no volume mount), then optionally applies the FK constraints.
# This is the one credentialed seeding step: published images and pgdata
# tarballs are vocab-free (FLIP#842/#843), so every environment loads the
# bundle it is licensed to hold. Idempotent: tables that already hold rows are
# skipped, and the constraints file guards itself with IF NOT EXISTS checks.
# The bundle is read only for the tables that actually need loading, so a run
# where everything is already present still applies the constraints without
# needing a bundle on disk at all.
#
# Usage: load_core_vocab.sh [--check] <vocab-dir> [constraints.sql]
#        load_core_vocab.sh --check
# Connection from env: OMOP_DB_HOST (default localhost), OMOP_DB_PORT,
# OMOP_POSTGRES_USER, OMOP_POSTGRES_PASSWORD, OMOP_POSTGRES_DB.
#
# --check only probes the database and loads nothing, exiting 0 when every
# table already holds the core vocabulary. It exists so a caller can decide
# whether it needs to fetch the multi-GB bundle at all (the Kubernetes
# vocab-load Job does exactly this). Any non-zero status — including an
# unreachable database — means "could not confirm", and callers must treat that
# as "fetch and load": this script re-runs the same guards on the real load, so
# a false "not loaded" costs a download while a false "loaded" would silently
# leave the platform without a vocabulary.

set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
  shift
fi

VOCAB_DIR="${1:-}"
CONSTRAINTS_FILE="${2:-}"
if [ "${CHECK_ONLY}" -eq 0 ] && [ -z "${VOCAB_DIR}" ]; then
  echo "usage: load_core_vocab.sh [--check] <vocab-dir> [constraints.sql]" >&2
  exit 1
fi

for var in OMOP_DB_PORT OMOP_POSTGRES_USER OMOP_POSTGRES_PASSWORD OMOP_POSTGRES_DB; do
  if [ -z "${!var:-}" ]; then
    echo "❌ ${var} must be set" >&2
    exit 1
  fi
done
if ! command -v psql > /dev/null 2>&1; then
  echo "❌ psql not found — install postgresql-client" >&2
  exit 1
fi

# Load order and COPY options match the retired image-init script
# (populate_vocabulary_tables.sql): Athena TSV with header, QUOTE E'\b'
# effectively disables quoting.
TABLES="CONCEPT_ANCESTOR CONCEPT_CLASS CONCEPT_RELATIONSHIP CONCEPT_SYNONYM CONCEPT DOMAIN DRUG_STRENGTH RELATIONSHIP VOCABULARY"

export PGPASSWORD="${OMOP_POSTGRES_PASSWORD}"
run_psql() {
  psql -v ON_ERROR_STOP=1 -h "${OMOP_DB_HOST:-localhost}" -p "${OMOP_DB_PORT}" \
       -U "${OMOP_POSTGRES_USER}" -d "${OMOP_POSTGRES_DB}" "$@"
}

# Per-table "already loaded" predicates. Four tables are SHARED with the DICOM
# vocabulary that ships inside the published pgdata tarballs, so a plain
# row-exists check would wrongly skip the core load on a freshly seeded trust —
# those use core-specific predicates instead (the DICOM rows live in the
# 2128xxxxxxx concept range / 'DICOM' vocabulary and never collide with the
# core bundle's rows, so a plain COPY appends cleanly alongside them).
core_present() {
  case "$1" in
    CONCEPT)
      run_psql -tAc "SELECT EXISTS (SELECT 1 FROM omop.concept WHERE vocabulary_id = 'SNOMED')" ;;
    VOCABULARY)
      run_psql -tAc "SELECT EXISTS (SELECT 1 FROM omop.vocabulary WHERE vocabulary_id <> 'DICOM')" ;;
    CONCEPT_CLASS)
      run_psql -tAc "SELECT EXISTS (SELECT 1 FROM omop.concept_class WHERE concept_class_id NOT IN ('DICOM Attributes', 'DICOM Value Sets'))" ;;
    CONCEPT_RELATIONSHIP)
      run_psql -tAc "SELECT EXISTS (SELECT 1 FROM omop.concept_relationship WHERE concept_id_1 < 2128000000 AND concept_id_2 < 2128000000)" ;;
    *)
      run_psql -tAc "SELECT EXISTS (SELECT 1 FROM omop.$1)" ;;
  esac
}

# Pass 1 — ask the database what is missing. Nothing is read from the bundle
# here, so this is also the whole of --check: the expensive fetch a caller may
# be about to do can be decided on one round-trip per table.
PENDING=""
for table in ${TABLES}; do
  # Deliberately a plain assignment, not `if [ "$(core_present …)" = "t" ]`:
  # a command substitution inside an `if` condition is exempt from `set -e`, so
  # a psql that failed for any reason (server restart, connection limit) would
  # yield "", compare unequal to "t", and fall straight through to the COPY —
  # silently duplicating rows in the four tables that carry no primary key.
  present="$(core_present "${table}")"
  case "${present}" in
    t)
      echo "⏭️  omop.${table} already holds the core vocabulary — skipping."
      ;;
    f)
      PENDING="${PENDING} ${table}"
      ;;
    *)
      echo "❌ unexpected guard result for omop.${table}: '${present}'" >&2
      exit 1
      ;;
  esac
done

if [ "${CHECK_ONLY}" -eq 1 ]; then
  if [ -n "${PENDING}" ]; then
    echo "📋 Core vocabulary still to load:${PENDING}"
    exit 1
  fi
  echo "✅ Core vocabulary already present in every table."
  exit 0
fi

# No bundle at all is a different fault from an incomplete one, and worth saying
# so: a caller that probed first (see --check) skips fetching the bundle when it
# believes everything is loaded, so reaching here means the probe and this run
# disagreed. Blaming "an incomplete bundle" would send the operator to inspect an
# artifact that was deliberately never downloaded.
if [ -n "${PENDING}" ] && [ ! -d "${VOCAB_DIR}" ]; then
  echo "❌ ${VOCAB_DIR} does not exist, but these tables still need loading:${PENDING}" >&2
  exit 1
fi

# Fail fast on an incomplete bundle before loading any of it — but only over the
# tables actually being loaded, so a no-op run still reaches the constraints
# below (which is the point: a previous run that loaded every table and then
# died before applying them must be recoverable by re-running).
for table in ${PENDING}; do
  if [ ! -f "${VOCAB_DIR}/${table}.csv" ]; then
    echo "❌ ${VOCAB_DIR}/${table}.csv missing — is this a complete vocabulary bundle?" >&2
    exit 1
  fi
done

for table in ${PENDING}; do
  echo "📥 Loading omop.${table} ..."
  run_psql -c "COPY omop.${table} FROM STDIN WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b')" \
    < "${VOCAB_DIR}/${table}.csv"
done

if [ -n "${CONSTRAINTS_FILE}" ]; then
  echo "🔗 Applying OMOP CDM constraints ..."
  run_psql -f "${CONSTRAINTS_FILE}"
fi

echo "✅ Core vocabulary load complete."
