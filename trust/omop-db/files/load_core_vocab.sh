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
#
# Usage: load_core_vocab.sh <vocab-dir> [constraints.sql]
# Connection from env: OMOP_DB_HOST (default localhost), OMOP_DB_PORT,
# OMOP_POSTGRES_USER, OMOP_POSTGRES_PASSWORD, OMOP_POSTGRES_DB.

set -euo pipefail

VOCAB_DIR="${1:?usage: load_core_vocab.sh <vocab-dir> [constraints.sql]}"
CONSTRAINTS_FILE="${2:-}"

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

for table in ${TABLES}; do
  if [ ! -f "${VOCAB_DIR}/${table}.csv" ]; then
    echo "❌ ${VOCAB_DIR}/${table}.csv missing — is this a complete vocabulary bundle?" >&2
    exit 1
  fi
done

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

for table in ${TABLES}; do
  if [ "$(core_present "${table}")" = "t" ]; then
    echo "⏭️  omop.${table} already holds the core vocabulary — skipping."
    continue
  fi
  echo "📥 Loading omop.${table} ..."
  run_psql -c "COPY omop.${table} FROM STDIN WITH (FORMAT CSV, HEADER, DELIMITER E'\t', QUOTE E'\b')" \
    < "${VOCAB_DIR}/${table}.csv"
done

if [ -n "${CONSTRAINTS_FILE}" ]; then
  echo "🔗 Applying OMOP CDM constraints ..."
  run_psql -f "${CONSTRAINTS_FILE}"
fi

echo "✅ Core vocabulary load complete."
