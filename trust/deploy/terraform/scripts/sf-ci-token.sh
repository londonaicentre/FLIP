#!/usr/bin/env bash
#
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
# Sourced, not run. sf_ci_refresh_token puts a fresh GitHub OIDC token in SNOWFLAKE_TOKEN (and,
# with a connection name, in SNOWFLAKE_CONNECTIONS_<NAME>_TOKEN, where snow reads it for a named
# connection).
#
# Why: a GitHub OIDC token lives five minutes and cannot be extended. snowflakedb/snowflake-actions
# mints one when its step runs, so any job longer than that (a refreshed plan of two roots, an image
# mirror) would present an expired token to Snowflake. Each caller refreshes immediately before it
# connects instead. Outside GitHub Actions (no ACTIONS_ID_TOKEN_REQUEST_URL) it does nothing, so an
# operator's key-pair connection is untouched.
#
# The token is masked in the job log before it is exported, and never written to a file or argv.

sf_ci_refresh_token() {
  local connection=${1:-}
  [ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ] || return 0
  if [ -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]; then
    echo "❌ The job has no id-token permission, so no Snowflake login is possible." >&2
    return 1
  fi
  local audience=${SNOWFLAKE_AUDIENCE:-snowflakecomputing.com}
  local token
  token=$(curl -sSf --max-time 30 \
    -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=$audience" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["value"])') || {
    echo "❌ Could not mint a GitHub OIDC token for Snowflake." >&2
    return 1
  }
  echo "::add-mask::$token"
  export SNOWFLAKE_TOKEN=$token
  if [ -n "$connection" ]; then
    local upper
    upper=$(printf '%s' "$connection" | tr '[:lower:]-' '[:upper:]_')
    export "SNOWFLAKE_CONNECTIONS_${upper}_TOKEN=$token"
  fi
}
