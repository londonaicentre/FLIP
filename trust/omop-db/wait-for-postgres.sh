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

# wait-for-postgres.sh <port> — block until Postgres answers on localhost:<port>.
# Bounded (default 180 x 5s = 15 min: a FIRST container boot runs the whole
# init chain including the multi-GB vocabulary load before the server accepts
# TCP connections; override via WAIT_FOR_POSTGRES_ATTEMPTS). Strict about its
# argument: an empty port would make pg_isready fall back to 5432 and report
# readiness of an unrelated server.

set -e

max_attempts="${WAIT_FOR_POSTGRES_ATTEMPTS:-180}"

if [ -z "${1:-}" ]; then
  >&2 echo "❌ wait-for-postgres.sh: no port given (is OMOP_DB_PORT_TRUST_<N> set in .env.build?)"
  exit 1
fi
if ! command -v pg_isready >/dev/null 2>&1; then
  >&2 echo "❌ pg_isready not found — install postgresql-client"
  exit 1
fi

attempts=0
>&2 echo "⏳ Waiting for Postgres to be available on port $1 (first init loads the vocabulary — can take minutes)..."
until pg_isready -h localhost -p "$1" >/dev/null 2>&1; do
  attempts=$((attempts + 1))
  if [ "$attempts" -ge "$max_attempts" ]; then
    >&2 echo "❌ Postgres not ready on port $1 after $attempts attempts — giving up"
    exit 1
  fi
  >&2 echo "  ... still waiting on port $1 (attempt $attempts/$max_attempts)"
  sleep 5
done

>&2 echo "✅ Postgres is up"
