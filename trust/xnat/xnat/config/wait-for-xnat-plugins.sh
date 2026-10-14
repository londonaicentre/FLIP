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

# Blocks until XNAT is ready for configure-xnat.sh to configure its plugins, so it never configures
# a plugin XNAT has not loaded.
#
# Beside XNAT — running in the container whose Tomcat loads ${XNAT_HOME}/plugins, which is every
# Compose path (dev, EC2, on-prem, upgrade-xnat, xnat-reset and xnat-configure all `docker exec`
# into xnat-web) — readiness is two authenticated probes, the second asked only once the first has
# passed:
#
#   1. GET /xapi/plugins lists every plugin id the jars in ${XNAT_HOME}/plugins declare. This is
#      XNAT's own answer to "which plugins did you load" (XnatPluginApi): a map keyed by plugin id,
#      built from the META-INF/xnat/*-plugin.properties files XNAT found on its classpath at
#      startup. The expected ids are read from those same files in the jars, not restated here, and
#      differ from the jar names (container-service-3.8.1-fat.jar is `containers`). The jars are
#      the very directory XNAT loads: filled from trust/xnat/artifacts.manifest, baked into the
#      image or bind-mounted from the dev cache.
#   2. GET /xapi/dqr/settings answers. The listing alone does not prove a plugin's REST routes are
#      registered: XNAT lists every plugin whose properties it found, but adds their Spring
#      configuration in a loop that a ClassNotFoundException abandons part-way
#      (XnatWebAppInitializer.getPluginConfigs in xnat-web 1.10.0), so a listed plugin can still
#      have no routes. DQR's settings route is the one configure-xnat.sh writes to next, so it is
#      the one probed.
#
# Anywhere else, readiness is probe 2 alone. The case is the chart's xnat-init job: it runs this
# script in its own pod, from the xnat-web image but without the xnat-web pod's plugins volume, so
# the jars it can see are the ones baked into the image — which the running XNAT never loads, since
# the chart mounts an emptyDir over ${XNAT_HOME}/plugins and fills it from xnat.web.plugins.urls.
# Expecting the baked roster there would wait out the whole budget, on every install and upgrade,
# for any plugin the image carries but the values do not: a values override that drops or replaces
# one, or an image newer than the chart (xnat.web.image.tag defaults to a moving tag). The loaded
# roster of the running pod is what `make -C trust/deploy/helm status` checks.
#
# "Beside XNAT" is decided once, up front, from two facts. A process in this PID namespace runs
# Tomcat (org.apache.catalina.startup.Bootstrap), and the plugin directory that Tomcat sees —
# /proc/<pid>/root + its -Dxnat.home + /plugins — is this script's ${XNAT_HOME}/plugins, the same
# inode. Seeing the process says "same container" only by default; the inode is the question the
# roster depends on, whether these are the jars XNAT loaded. A Kubernetes pod shares no PID
# namespace with another pod, so the xnat-init job always takes probe 2 alone, and says so.
# XNAT_PLUGIN_READINESS_PROC_DIR (default /proc) exists only as a test seam.
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
# transient rejection during boot. The first rejection also spends one attempt on the other
# credential, which is what keeps a retry against an already-rotated XNAT from paying that whole
# ~45s tolerance before it gets to the password that works.
#
# On credential handling, see the loop below: a route that is not answering yet gives 404, 5xx or
# no answer at all, so 401/403 can only mean the credentials are wrong — never "still loading".
# That distinction is what keeps this script from hammering XNAT with bad logins until the account
# locks. Only the first probe can spend a wrong password: the second is asked with a password the
# first has just accepted.

: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}"
PLUGINS_URL="${XNAT_URL}/xapi/plugins"
DQR_URL="${XNAT_URL}/xapi/dqr/settings"
PLUGIN_DIR="${XNAT_HOME:-/data/xnat/home}/plugins"
PROC_DIR="${XNAT_PLUGIN_READINESS_PROC_DIR:-/proc}"
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

# Succeeds when a Tomcat in this PID namespace loads its plugins from PLUGIN_DIR (see the header).
# A process vanishing mid-scan, or a /proc entry this user may not read, simply does not count.
beside_xnat() {
  local cmdline entry args home
  for cmdline in "$PROC_DIR"/[0-9]*/cmdline; do
    args=$(tr '\0' '\n' 2>/dev/null < "$cmdline") || continue
    grep -qx 'org.apache.catalina.startup.Bootstrap' <<< "$args" || continue
    entry="${cmdline%/cmdline}"
    home=$(sed -n 's/^-Dxnat\.home=//p' <<< "$args" | tail -n 1)
    if [[ "$entry/root${home:-${XNAT_HOME:-/data/xnat/home}}/plugins" -ef "$PLUGIN_DIR" ]]; then
      return 0
    fi
  done
  return 1
}

check_roster=""
if beside_xnat; then
  check_roster=1
else
  echo "No Tomcat beside this script loads $PLUGIN_DIR (as for the chart's xnat-init job, which runs in" \
       "its own pod), so those jars are not the ones XNAT loaded: waiting for the DQR plugin's settings" \
       "route only — make -C trust/deploy/helm status checks the plugins the running XNAT loaded."
fi

# Prints the plugin ids a jar declares, one per line: the `id` of each properties file under
# META-INF/xnat/ named *-plugin.properties — the pattern XNAT scans
# (XnatPluginBeanManager.PATTERN_PLUGIN_BEAN_PROPERTIES). unzip's `*` crosses `/`, as XNAT's `**`
# does. Prints nothing for a jar that is not an XNAT plugin (a library jar), for which unzip exits
# 11; the caller has already proved the jar is a readable zip.
declared_plugin_ids() {
  { unzip -p "$1" 'META-INF/xnat/*-plugin.properties' 2>/dev/null || true; } \
    | tr -d '\r' \
    | sed -n 's/^[[:space:]]*id[[:space:]]*[=:][[:space:]]*\([^[:space:]]*\).*$/\1/p'
}

expected_ids=()
if [[ -n "$check_roster" ]]; then
  # Both ship in the xnat-web image (trust/xnat/xnat/Dockerfile). Checked here so a missing binary
  # fails naming itself, rather than reading as "this jar declares no plugin" or "XNAT listed
  # nothing". Probe 2 alone needs neither.
  for tool in unzip jq; do
    command -v "$tool" >/dev/null || { echo "ERROR: $tool is required by wait-for-xnat-plugins.sh" >&2; exit 1; }
  done

  shopt -s nullglob
  for jar in "$PLUGIN_DIR"/*.jar; do
    # 1 is unzip's warning status — an archive it can read despite a defect, such as bytes prepended
    # to it, which Java's jar loader reads too. Above that (9: no central directory, the mark of a
    # truncated download) XNAT cannot load the jar either, and which plugin it was meant to be is
    # unknowable from here, so nothing could tell this wait what to expect from it.
    zip_status=0
    unzip -Z1 "$jar" >/dev/null 2>&1 || zip_status=$?
    if (( zip_status > 1 )); then
      echo "ERROR: $jar is not a readable jar (unzip exit $zip_status: a truncated or failed download?)." >&2
      echo "  XNAT cannot load it, and this wait cannot tell which plugin it should have been." >&2
      exit 1
    fi
    ids=$(declared_plugin_ids "$jar")
    if [[ -z "$ids" ]]; then
      echo "  $(basename "$jar") declares no XNAT plugin — not waited for."
      continue
    fi
    while IFS= read -r id; do
      expected_ids+=("$id")
    done <<< "$ids"
  done
  shopt -u nullglob

  if (( ${#expected_ids[@]} == 0 )); then
    # Every FLIP XNAT runs plugins (DQR at the least), so an empty roster is a broken install, not a
    # vacuously ready one — and waiting cannot put a jar on disk.
    echo "ERROR: no XNAT plugin jars found in $PLUGIN_DIR, the directory this XNAT loads its plugins from." >&2
    echo "  Every FLIP XNAT runs plugins (DQR at the least). Where they come from depends on the" >&2
    echo "  deployment: the dev plugin cache in Compose development (make -C trust prepare-artifacts)," >&2
    echo "  the image itself in every other Compose deployment (a custom image must bake them in, as" >&2
    echo "  trust/xnat/xnat/Dockerfile does), and xnat.web.plugins.urls in the Helm chart's xnat-web pod." >&2
    exit 1
  fi
  sorted_ids=$(printf '%s\n' "${expected_ids[@]}" | LC_ALL=C sort -u)
  expected_ids=()
  while IFS= read -r id; do
    expected_ids+=("$id")
  done <<< "$sorted_ids"
fi

# GETs one route with one admin password. Prints the response body, a newline, then the HTTP
# status; the status is 000 when curl got no answer (refused, timed out), in which case curl's own
# -w output and the fallback below both end in it, so the last line is the status either way.
xnat_get() {
  curl -sS --connect-timeout 5 --max-time 10 -w '\n%{http_code}' \
    -u "${XNAT_ADMIN_USER}:$2" "$1" || printf '\n000'
}

# One readiness attempt with one admin password. Sets, for the caller:
#   attempt_status         HTTP status of the request that decided the attempt
#   attempt_url            the route that decided it
#   attempt_authenticated  1 when XNAT accepted the password (/xapi/plugins, or the DQR route when
#                          that is the only probe), whatever came after
#   attempt_missing        expected plugin ids XNAT did not list (empty unless it answered 2xx)
attempt() {
  local password="$1" response loaded id
  attempt_authenticated=""
  attempt_missing=""

  if [[ -n "$check_roster" ]]; then
    attempt_url="$PLUGINS_URL"
    response=$(xnat_get "$PLUGINS_URL" "$password")
    attempt_status="${response##*$'\n'}"
    [[ "$attempt_status" == 2* ]] || return 0
    attempt_authenticated=1

    # A body that is not a JSON object lists nothing, so every expected plugin reads as missing
    # rather than the wait passing on a response it could not read.
    loaded=$(printf '%s' "${response%$'\n'*}" \
      | jq -r 'if type == "object" then keys[] else empty end' 2>/dev/null) || loaded=""
    for id in "${expected_ids[@]}"; do
      grep -Fxq -- "$id" <<< "$loaded" || attempt_missing+="${attempt_missing:+ }$id"
    done
    [[ -z "$attempt_missing" ]] || return 0
  fi

  attempt_url="$DQR_URL"
  response=$(xnat_get "$DQR_URL" "$password")
  attempt_status="${response##*$'\n'}"
  if [[ "$attempt_status" == 2* ]]; then
    attempt_authenticated=1
  fi
}

# What a ready XNAT reports, for the probes this run made. $1 is the HTTP status; $2 an optional note.
announce_ready() {
  if [[ -n "$check_roster" ]]; then
    echo "XNAT plugins are ready (HTTP $1$2): XNAT loaded ${expected_ids[*]} and the DQR settings route" \
         "answers."
  else
    echo "XNAT DQR plugin is ready (HTTP $1$2)."
  fi
}

# Only called on the fatal redirect path, to name the destination in the error. Worth one extra
# request there because "302" alone does not say *why*, and the destination (/setup) does.
probe_redirect_target() {
  curl -sS -o /dev/null -w '%{redirect_url}' \
    --connect-timeout 5 --max-time 10 \
    -u "${XNAT_ADMIN_USER}:${credential}" "$last_url" 2>/dev/null || true
}

if [[ -n "$check_roster" ]]; then
  echo "Waiting for XNAT to load its plugins (${expected_ids[*]}, declared by the jars in" \
       "$PLUGIN_DIR) and for the DQR plugin's settings route..."
  last_url="$PLUGINS_URL"
else
  echo "Waiting for the XNAT DQR plugin to be ready..."
  last_url="$DQR_URL"
fi
wait_start=$SECONDS
last_status="000"
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
        announce_ready "$last_status" ""
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
      # Only an authenticated answer acts on the result. Ready is ready, whichever admin password
      # proved it: configure-xnat.sh re-establishes its own credential immediately afterwards.
      # Accepted but not ready yet (/xapi/plugins answered, but a plugin is still missing or the DQR
      # route is not answering) proves this is the password XNAT holds — one account has one
      # password — so the wait carries on with it rather than spending more of the lockout budget on
      # the one just rejected. Anything else is inconclusive — during a boot blip neither password
      # gets an answer — so the run-of-rejections logic below keeps the decision to switch, and the
      # blip stays forgiven. (With the DQR route as the only probe, accepted means ready.)
      if [[ -n "$alternate" && -z "$alternate_probed" ]]; then
        alternate_probed=1
        attempt "$alternate"
        if [[ -n "$attempt_authenticated" ]]; then
          if [[ "$attempt_status" == 2* && -z "$attempt_missing" ]]; then
            announce_ready "$attempt_status" ", $alternate_label admin password"
            exit 0
          fi
          echo "  XNAT accepted the $alternate_label admin password and rejected the" \
               "$credential_label one — continuing with the $alternate_label one."
          credential="$alternate"
          credential_label="$alternate_label"
          alternate=""
          auth_failures=0
          last_status="$attempt_status"
          last_url="$attempt_url"
          last_missing="$attempt_missing"
          sleep_for="$POLL_SECONDS"
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
      # 404 (route not registered yet), 000 (transport), 5xx (still starting): not a credential
      # problem, so forgive any earlier blip and keep polling the same credential.
      auth_failures=0
      ;;
  esac

  if (( SECONDS - wait_start >= 10#$TIMEOUT_SECONDS )); then
    echo "ERROR: XNAT plugins did not become ready within $((SECONDS - wait_start))s." >&2
    if [[ -n "$last_missing" ]]; then
      # XNAT builds that listing once, at startup, so a plugin still missing now is not loading
      # slowly: XNAT did not find it, and only a restart after the cause is fixed will add it.
      echo "  XNAT has not loaded: $last_missing" >&2
      echo "  (expected because a jar in $PLUGIN_DIR declares it; XNAT lists the plugins it" >&2
      echo "  found at startup, so check the xnat-web startup log for that jar.)" >&2
    fi
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
