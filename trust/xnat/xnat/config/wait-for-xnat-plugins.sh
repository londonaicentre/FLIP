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
# With XNAT_PLUGIN_READINESS_ROSTER=1 it first waits for GET /xapi/plugins to list every plugin the
# jars in ${XNAT_HOME}/plugins declare (the `id=` in each jar's META-INF/xnat/*-plugin.properties,
# which is what XNAT lists). Those jars are the ones XNAT loaded only inside the xnat-web container,
# so only the Compose recipe that configures from there sets the flag (trust/xnat/Makefile
# xnat-configure). The chart's xnat-init pod sees the image's baked jars, not the pod's
# xnat.web.plugins.urls; `make -C trust/deploy/helm status` checks the loaded roster there instead.
# The DQR route is probed either way, because a listed plugin can still have no routes: XNAT lists
# every plugin it discovered, but one ClassNotFoundException ends its config-loading loop
# (XnatWebAppInitializer.getPluginConfigs, xnat-web 1.10.0).
#
# Tunable via the environment (defaults suit an unattended prod deploy; shorten the timeout for a
# tighter dev feedback loop):
#   XNAT_PLUGIN_READINESS_TIMEOUT_SECONDS      overall wait budget                  (default 900)
#   XNAT_PLUGIN_READINESS_POLL_SECONDS         delay between probes                 (default 5)
#   XNAT_PLUGIN_READINESS_AUTH_FAILURE_LIMIT   consecutive 401/403s per credential  (default 3)
#   XNAT_PLUGIN_READINESS_AUTH_BACKOFF_SECONDS delay after a rejected login         (default 15)
#   XNAT_PLUGIN_READINESS_ROSTER               1 to also wait for the jar roster    (default off)
#
# The two auth knobs bound wrong-password logins at AUTH_FAILURE_LIMIT per credential — 6 in total
# against XNAT's 20-attempt lockout — while the backoff still buys ~45s of tolerance for a
# transient rejection during boot. The first rejection also spends one attempt on the other
# credential, which is what keeps a retry against an already-rotated XNAT from paying that whole
# ~45s tolerance before it gets to the password that works.
#
# On credential handling, see the loop below: a not-yet-registered route answers 404, so 401/403 can
# only mean the credentials are wrong — never "still loading". That distinction is what keeps this
# script from hammering XNAT with bad logins until the account locks.

: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}"
PLUGINS_URL="${XNAT_URL}/xapi/plugins"
DQR_URL="${XNAT_URL}/xapi/dqr/settings"
PLUGIN_DIR="${XNAT_HOME:-/data/xnat/home}/plugins"
ROSTER="${XNAT_PLUGIN_READINESS_ROSTER:-}"
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
     && "$AUTH_BACKOFF_SECONDS" =~ ^(0|[1-9][0-9]*)([.][0-9]+)?$ \
     && "$ROSTER" =~ ^[01]?$ ]]; then
  echo "ERROR: XNAT plugin readiness tuning values must be non-negative numbers without leading" >&2
  echo "  zeros, the auth-failure limit at least 1, and the roster flag 0 or 1 (got" >&2
  echo "  timeout='$TIMEOUT_SECONDS' poll='$POLL_SECONDS'" >&2
  echo "  auth-limit='$AUTH_FAILURE_LIMIT' auth-backoff='$AUTH_BACKOFF_SECONDS' roster='$ROSTER')." >&2
  exit 1
fi

# The roster, read from the jars. They are checksum-pinned (dev cache) or baked at build, so one
# failure covers every way a jar can fail to name its plugin.
expected_ids=()
if [[ "$ROSTER" == 1 ]]; then
  for tool in unzip jq; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool is required by wait-for-xnat-plugins.sh" >&2; exit 1; }
  done
  shopt -s nullglob
  for jar in "$PLUGIN_DIR"/*.jar; do
    id=$(unzip -p "$jar" 'META-INF/xnat/*-plugin.properties' 2>/dev/null \
      | sed -n 's/^[[:space:]]*id[[:space:]]*[=:][[:space:]]*\([^[:space:]]*\).*$/\1/p' | head -n 1) || true
    [[ -n "$id" ]] || { echo "ERROR: cannot read an XNAT plugin id from $jar." >&2; exit 1; }
    expected_ids+=("$id")
  done
  shopt -u nullglob
  (( ${#expected_ids[@]} )) || { echo "ERROR: no XNAT plugin jars in $PLUGIN_DIR." >&2; exit 1; }
fi

# GETs one route with one admin password. Prints the response body, a newline, then the HTTP
# status; the status is 000 when curl got no answer (refused, timed out), in which case curl's own
# -w output and the fallback below both end in it, so the last line is the status either way.
xnat_get() {
  curl -sS --connect-timeout 5 --max-time 10 -w '\n%{http_code}' \
    -u "${XNAT_ADMIN_USER}:$2" "$1" || printf '\n000'
}

# One readiness attempt with one admin password. Sets attempt_status and attempt_url (the request
# that decided it) and attempt_missing (expected ids /xapi/plugins did not list).
attempt() {
  local response loaded id
  attempt_missing=""
  if [[ "$ROSTER" == 1 ]]; then
    attempt_url="$PLUGINS_URL"
    response=$(xnat_get "$PLUGINS_URL" "$1")
    attempt_status="${response##*$'\n'}"
    [[ "$attempt_status" == 2* ]] || return 0
    # A body that is not a JSON object lists nothing, so every plugin reads as missing.
    loaded=$(printf '%s' "${response%$'\n'*}" | jq -r 'keys[]?' 2>/dev/null) || loaded=""
    for id in "${expected_ids[@]}"; do
      grep -Fxq -- "$id" <<< "$loaded" || attempt_missing+="${attempt_missing:+ }$id"
    done
    [[ -z "$attempt_missing" ]] || return 0
  fi
  attempt_url="$DQR_URL"
  response=$(xnat_get "$DQR_URL" "$1")
  attempt_status="${response##*$'\n'}"
}

# Only called on the fatal redirect path, to name the destination in the error. Worth one extra
# request there because "302" alone does not say *why*, and the destination (/setup) does.
probe_redirect_target() {
  curl -sS -o /dev/null -w '%{redirect_url}' \
    --connect-timeout 5 --max-time 10 \
    -u "${XNAT_ADMIN_USER}:${credential}" "$last_url" 2>/dev/null || true
}

if [[ "$ROSTER" == 1 ]]; then
  ready_means="XNAT loaded ${expected_ids[*]} and the DQR settings route answers"
  echo "Waiting for XNAT to load ${expected_ids[*]} (the jars in $PLUGIN_DIR) and for the DQR plugin..."
else
  ready_means="the DQR settings route answers"
  echo "Waiting for the XNAT DQR plugin to be ready..."
fi
wait_start=$SECONDS
last_status="000"
last_url="$DQR_URL"
last_missing=""

# Probe with one credential at a time. This script runs before configure-xnat.sh rotates the admin
# password, so the initial password is the right guess on a fresh instance; on a re-run against an
# already-configured XNAT it is the rotated one. Reacting only to 401/403, never to 404, is what
# bounds the wrong-password logins at AUTH_FAILURE_LIMIT per credential instead of one per poll for
# the whole timeout — the latter trips XNAT's 20-attempt/1-hour account lockout in ~100s and bricks
# the deploy.
credential_label="initial"
credential="$XNAT_ADMIN_INITIAL_PASSWORD"
alternate_label="configured"
alternate="$XNAT_ADMIN_PASSWORD"
# Dev kits set both passwords to the same value: there is then no rotation to detect, and a
# persistent 401 is a genuine credential fault rather than a signal to switch.
[[ "$alternate" == "$credential" ]] && alternate=""
auth_failures=0
alternate_probed=""
alternate_failures=0

while true; do
  attempt "$credential"
  last_status="$attempt_status"
  last_url="$attempt_url"
  last_missing="$attempt_missing"
  sleep_for="$POLL_SECONDS"

  case "$last_status" in
    2*)
      if [[ -z "$last_missing" ]]; then
        echo "XNAT plugins are ready (HTTP $last_status): $ready_means."
        exit 0
      fi
      # Authenticated, so not a credential problem: forgive any earlier blip and keep polling.
      auth_failures=0
      ;;
    401 | 403)
      # Back off further than the readiness poll: XNAT can answer 401 for a beat while its auth
      # providers finish wiring, so tolerating a blip should cost wall-clock time, not lockout
      # budget. Only a *run* of rejections is treated as proof the credential is wrong.
      auth_failures=$((auth_failures + 1))
      sleep_for="$AUTH_BACKOFF_SECONDS"

      # A rejection is the one moment the other password is worth spending an attempt on, so spend
      # it here rather than after a full run. Re-running configuration against an already-rotated
      # XNAT — the idempotent retry this whole script has to support, and the one repeated while
      # debugging a stag deploy — starts on the wrong password by construction, and waiting out the
      # run first costs AUTH_FAILURE_LIMIT rejected logins and the whole backoff tolerance before
      # reaching the password that works.
      #
      # Only a ready answer acts on the result: whether XNAT is ready is the entire question this
      # script asks, and which admin password proved it does not matter to configure-xnat.sh, which
      # re-establishes its own credential immediately afterwards. Anything else is inconclusive —
      # during a boot blip the route still 404s for both passwords — so the run-of-rejections logic
      # below keeps the decision to switch, and the blip stays forgiven.
      if [[ -n "$alternate" && -z "$alternate_probed" ]]; then
        alternate_probed=1
        attempt "$alternate"
        if [[ "$attempt_status" == 2* && -z "$attempt_missing" ]]; then
          echo "XNAT plugins are ready (HTTP $attempt_status, $alternate_label admin password): $ready_means."
          exit 0
        elif [[ "$attempt_status" == 401 || "$attempt_status" == 403 ]]; then
          # Charged to the credential it was spent on, and carried into the switch below, so both
          # passwords still share one AUTH_FAILURE_LIMIT budget each however the failures fall.
          alternate_failures=1
        fi
      fi

      if (( auth_failures >= 10#$AUTH_FAILURE_LIMIT )); then
        if [[ -n "$alternate" ]]; then
          echo "  XNAT rejected the $credential_label admin password ${auth_failures}x" \
               "(HTTP $last_status) — switching to the $alternate_label one."
          credential="$alternate"
          credential_label="$alternate_label"
          alternate=""
          auth_failures=$alternate_failures
        else
          echo "ERROR: XNAT rejected every admin credential (HTTP $last_status)." >&2
          echo "  endpoint: $last_url" >&2
          echo "  user:     $XNAT_ADMIN_USER" >&2
          echo "  Waiting cannot turn a rejected password into an accepted one — correct" >&2
          echo "  XNAT_ADMIN_INITIAL_PASSWORD / XNAT_ADMIN_PASSWORD in the kit file. Stopping" >&2
          echo "  here so repeated failed logins do not lock the admin account for an hour." >&2
          exit 1
        fi
      fi
      ;;
    3*)
      # An uninitialized XNAT redirects EVERY authenticated route to /setup — not just plugin
      # routes; /data/projects does it too. So a redirect here says nothing about plugins and
      # everything about the site never having been switched on.
      #
      # Fail immediately rather than poll. Waiting cannot clear it: the step that would —
      # POST /xapi/siteConfig {"initialized": true} in configure-xnat.sh — runs *after* this
      # wait, so a retry loop is waiting on something downstream of itself. Treating it as
      # transient is what made this cost a full timeout and then report a healthy plugin as
      # "not ready" (FLIP#966).
      redirect_target="$(probe_redirect_target)"
      echo "ERROR: XNAT redirected the readiness probe (HTTP $last_status)." >&2
      echo "  endpoint: $last_url" >&2
      [[ -n "$redirect_target" ]] && echo "  redirected to: $redirect_target" >&2
      echo "  This means XNAT is not initialized yet — every authenticated route redirects to" >&2
      echo "  /setup until site initialization runs, so no amount of waiting will help. This" >&2
      echo "  readiness wait must run AFTER configure-xnat.sh initializes the site." >&2
      exit 1
      ;;
    *)
      # 404 (plugin route not registered yet), 000 (transport), 5xx (still starting): not a
      # credential problem, so forgive any earlier blip and keep polling the same credential.
      auth_failures=0
      ;;
  esac

  if (( SECONDS - wait_start >= 10#$TIMEOUT_SECONDS )); then
    echo "ERROR: XNAT plugins did not become ready within $((SECONDS - wait_start))s." >&2
    # XNAT lists its plugins once, at startup, so one still missing now will not load by waiting.
    [[ -z "$last_missing" ]] || echo "  XNAT has not loaded: $last_missing (see the xnat-web startup log)" >&2
    echo "  endpoint:   $last_url" >&2
    echo "  credential: $credential_label" >&2
    echo "  last HTTP status: $last_status" >&2
    exit 1
  fi

  if [[ -n "$last_missing" ]]; then
    echo "  XNAT has not loaded every plugin yet (missing: $last_missing); retrying..."
  else
    echo "  Not ready yet (HTTP $last_status from $last_url); retrying..."
  fi
  sleep "$sleep_for"
done
