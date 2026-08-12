#!/bin/bash
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

set -euo pipefail

# Blocks until XNAT's DQR plugin route answers, so configure-xnat.sh never POSTs plugin settings at
# a Tomcat that is up but has not registered its plugins yet.
#
# Tunable via the environment (defaults suit an unattended prod deploy; shorten the timeout for a
# tighter dev feedback loop):
#   XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS      overall wait budget                  (default 900)
#   XNAT_PLUGIN_READINESS_POLL_SECONDS         delay between probes                 (default 5)
#   XNAT_PLUGIN_READINESS_AUTH_FAILURE_LIMIT   consecutive 401/403s per credential  (default 3)
#   XNAT_PLUGIN_READINESS_AUTH_BACKOFF_SECONDS delay after a rejected login         (default 15)
#
# The two auth knobs bound wrong-password logins at AUTH_FAILURE_LIMIT per credential — 6 in total
# against XNAT's 20-attempt lockout — while the backoff still buys ~45s of tolerance for a
# transient rejection during boot.
#
# On credential handling, see the loop below: a not-yet-registered route answers 404, so 401/403 can
# only mean the credentials are wrong — never "still loading". That distinction is what keeps this
# script from hammering XNAT with bad logins until the account locks.

: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}"
READINESS_URL="${XNAT_URL}/xapi/dqr/settings"
TIMEOUT_SECONDS="${XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${XNAT_PLUGIN_READINESS_POLL_SECONDS:-5}"
AUTH_FAILURE_LIMIT="${XNAT_PLUGIN_READINESS_AUTH_FAILURE_LIMIT:-3}"
AUTH_BACKOFF_SECONDS="${XNAT_PLUGIN_READINESS_AUTH_BACKOFF_SECONDS:-15}"

# Leading zeros are rejected rather than normalised: bash arithmetic reads "09" as an invalid octal
# literal, and `(( ))` returning non-zero on the timeout comparison would read as "not timed out" —
# an unbounded loop. Every arithmetic use below is also 10#-prefixed as a second line of defence.
if ! [[ "$TIMEOUT_SECONDS" =~ ^(0|[1-9][0-9]*)$ \
     && "$POLL_SECONDS" =~ ^(0|[1-9][0-9]*)([.][0-9]+)?$ \
     && "$AUTH_FAILURE_LIMIT" =~ ^[1-9][0-9]*$ \
     && "$AUTH_BACKOFF_SECONDS" =~ ^(0|[1-9][0-9]*)([.][0-9]+)?$ ]]; then
  echo "ERROR: XNAT plugin readiness tuning values must be non-negative numbers without leading" >&2
  echo "  zeros, and the auth-failure limit must be at least 1 (got" >&2
  echo "  timeout='$TIMEOUT_SECONDS' poll='$POLL_SECONDS'" >&2
  echo "  auth-limit='$AUTH_FAILURE_LIMIT' auth-backoff='$AUTH_BACKOFF_SECONDS')." >&2
  exit 1
fi

probe() {
  local password="$1"
  local status
  status=$(curl -sS -o /dev/null -w '%{http_code}' \
    --connect-timeout 5 --max-time 10 \
    -u "${XNAT_ADMIN_USER}:${password}" "$READINESS_URL") || status="000"
  printf '%s' "$status"
}

echo "Waiting for the XNAT DQR plugin to be ready..."
wait_start=$SECONDS
last_status="000"

# Probe with one credential at a time. This script runs before configure-xnat.sh rotates the admin
# password, so the initial password is the right guess on a fresh instance; on a re-run against an
# already-configured XNAT it is the rotated one. Switching on 401/403 (never on 404) means at most
# one wrong-password login per credential, instead of one per poll for the whole timeout — the
# latter trips XNAT's 20-attempt/1-hour account lockout in ~100s and bricks the deploy.
credential_label="initial"
credential="$XNAT_ADMIN_INITIAL_PASSWORD"
alternate="$XNAT_ADMIN_PASSWORD"
# Dev kits set both passwords to the same value: there is then no rotation to detect, and a
# persistent 401 is a genuine credential fault rather than a signal to switch.
[[ "$alternate" == "$credential" ]] && alternate=""
auth_failures=0

while true; do
  last_status=$(probe "$credential")
  sleep_for="$POLL_SECONDS"

  case "$last_status" in
    2*)
      echo "XNAT DQR plugin is ready (HTTP $last_status)."
      exit 0
      ;;
    401 | 403)
      # Back off further than the readiness poll: XNAT can answer 401 for a beat while its auth
      # providers finish wiring, so tolerating a blip should cost wall-clock time, not lockout
      # budget. Only a *run* of rejections is treated as proof the credential is wrong.
      auth_failures=$((auth_failures + 1))
      sleep_for="$AUTH_BACKOFF_SECONDS"

      if (( auth_failures >= 10#$AUTH_FAILURE_LIMIT )); then
        if [[ -n "$alternate" ]]; then
          echo "  XNAT rejected the $credential_label admin password ${auth_failures}x" \
               "(HTTP $last_status) — switching to the configured one."
          credential="$alternate"
          credential_label="configured"
          alternate=""
          auth_failures=0
        else
          echo "ERROR: XNAT rejected every admin credential (HTTP $last_status)." >&2
          echo "  endpoint: $READINESS_URL" >&2
          echo "  user:     $XNAT_ADMIN_USER" >&2
          echo "  Waiting cannot turn a rejected password into an accepted one — correct" >&2
          echo "  XNAT_ADMIN_INITIAL_PASSWORD / XNAT_ADMIN_PASSWORD in the kit file. Stopping" >&2
          echo "  here so repeated failed logins do not lock the admin account for an hour." >&2
          exit 1
        fi
      fi
      ;;
    *)
      # 404 (plugin route not registered yet), 000 (transport), 5xx (still starting): not a
      # credential problem, so forgive any earlier blip and keep polling the same credential.
      auth_failures=0
      ;;
  esac

  if (( SECONDS - wait_start >= 10#$TIMEOUT_SECONDS )); then
    echo "ERROR: XNAT DQR plugin did not become ready within $((SECONDS - wait_start))s." >&2
    echo "  endpoint:   $READINESS_URL" >&2
    echo "  credential: $credential_label" >&2
    echo "  last HTTP status: $last_status" >&2
    exit 1
  fi

  echo "  DQR not ready yet (HTTP $last_status); retrying..."
  sleep "$sleep_for"
done
