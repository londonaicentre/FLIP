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
#


# This script configures the XNAT instance
#
# Note the rest of the environment variables are available from the file
# - trust/xnat/.env
#

set -euo pipefail

# shellcheck source=./xnat-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/xnat-lib.sh"

# Required, non-empty (":?" also rejects set-but-empty, which `set -u` does
# not): an empty value interpolated into a JSON payload produces invalid JSON
# that XNAT rejects — the failure mode behind the silent PACS registration
# bug (FLIP#822 / FLIP#862).
: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"
: "${XNAT_SERVICE_USER:?}" "${XNAT_SERVICE_PASSWORD:?}" "${XNAT_PORT:?}"

# ${VAR-default} rather than ${VAR:-default} throughout: an *unset* variable takes the default,
# but one set to the empty string stays empty and trips the guard below. An operator who writes
# PACS_HOST= in a kit file must get a loud failure, not a silent fallback to the mocked PACS.
# The bare-dash form is also the marker test_deploy_wiring.py derives the operator-knob roster
# from, so XNAT_URL keeps the colon form deliberately: it is container-network wiring that
# neither compose nor the Helm init job exposes to kit files, not a knob.
#
# XNAT's own identity and the upstream PACS. Defaults reproduce the mocked Orthanc that ships for
# development, so an unconfigured deployment behaves exactly as before; a real trust overrides them
# from its kit file (Compose) or Helm values (Kubernetes).
#
# XNAT_AETITLE is XNAT's AE title in three places that must agree: the DICOM SCP receiver, the DQR
# calling AE, and the C-MOVE destination that imaging-api hands to the PACS. The PACS opens the
# C-STORE association addressed to the AE title it has registered, so a receiver configured under a
# different title rejects it.
XNAT_URL="${XNAT_URL:-http://xnat-web:8080}" # internal to the container network
XNAT_AETITLE="${XNAT_AETITLE-XNAT}"
PACS_HOST="${PACS_HOST-orthanc}"            # service name in compose / k8s, or a real PACS host
PACS_AETITLE="${PACS_AETITLE-ORTHANC}"
PACS_QR_PORT="${PACS_QR_PORT-4242}"
PACS_LABEL="${PACS_LABEL-Test PACS instance}"
# Relational queries / extended negotiation. On by default because both the mocked Orthanc and
# every PACS we have integrated with support it, but it is a genuine per-PACS capability: a PACS
# that does not support it must have this off or it rejects the association (FLIP#993).
PACS_SUPPORTS_EXTENDED_NEGOTIATIONS="${PACS_SUPPORTS_EXTENDED_NEGOTIATIONS-true}"

# DQR retry behaviour and the PACS availability schedule — the throttle for a production PACS, which
# may refuse further associations after a certain volume (FLIP#993). Defaults are today's values.
DQR_MAX_PACS_REQUEST_ATTEMPTS="${DQR_MAX_PACS_REQUEST_ATTEMPTS-100}"
DQR_RETRY_WAIT_SECONDS="${DQR_RETRY_WAIT_SECONDS-300}"
PACS_AVAILABILITY_DAYS="${PACS_AVAILABILITY_DAYS-MONDAY,TUESDAY,WEDNESDAY,THURSDAY,FRIDAY,SATURDAY,SUNDAY}"
PACS_AVAILABILITY_START="${PACS_AVAILABILITY_START-00:00}"
PACS_AVAILABILITY_END="${PACS_AVAILABILITY_END-24:00}"
PACS_THREADS="${PACS_THREADS-1}"
PACS_UTILIZATION_PERCENT="${PACS_UTILIZATION_PERCENT-100}"

# Same fail-loud contract as the credentials above: a default must never resolve to empty, or the
# interpolated JSON is malformed and XNAT rejects it silently (FLIP#822 / FLIP#862).
: "${XNAT_URL:?}" "${XNAT_AETITLE:?}" "${PACS_HOST:?}" "${PACS_AETITLE:?}" "${PACS_QR_PORT:?}"
: "${PACS_LABEL:?}" "${DQR_MAX_PACS_REQUEST_ATTEMPTS:?}" "${DQR_RETRY_WAIT_SECONDS:?}"
: "${PACS_AVAILABILITY_DAYS:?}" "${PACS_AVAILABILITY_START:?}" "${PACS_AVAILABILITY_END:?}"
: "${PACS_THREADS:?}" "${PACS_UTILIZATION_PERCENT:?}" "${PACS_SUPPORTS_EXTENDED_NEGOTIATIONS:?}"

# Validated here rather than left to jq: --argjson accepts any JSON, so a "yes" or "True" would
# fail inside the filter with a parse error that names neither the variable nor the accepted
# values, and a bare `1` would silently register as the number 1 rather than a boolean.
case "${PACS_SUPPORTS_EXTENDED_NEGOTIATIONS}" in
  true|false) ;;
  *) echo "ERROR: PACS_SUPPORTS_EXTENDED_NEGOTIATIONS must be true or false, got '${PACS_SUPPORTS_EXTENDED_NEGOTIATIONS}'" >&2; exit 1 ;;
esac

# The availability schedule is validated before XNAT is touched: the converge at the end removes
# intervals before it writes, so a value DQR refused part-way would leave days with no window. The
# accepted forms are DQR 3.0.0's: DayOfWeek names in capitals, HH:MM / H:MM / 24:00, and threads and
# utilisation of at least 1 (DQR stores 0 for both if either is 0, and 0% never runs). Commas and
# spaces both separate days.
read -r -a avail_day_list <<< "${PACS_AVAILABILITY_DAYS//,/ }"
if [[ ${#avail_day_list[@]} -eq 0 ]]; then
  echo "ERROR: PACS_AVAILABILITY_DAYS lists no days, got '${PACS_AVAILABILITY_DAYS}'. DQR retrieves only" \
    "inside a scheduled window, so an empty schedule would stop retrieval altogether." >&2
  exit 1
fi
for avail_day in "${avail_day_list[@]}"; do
  case "$avail_day" in
    MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY) ;;
    *) echo "ERROR: PACS_AVAILABILITY_DAYS must list days as MONDAY ... SUNDAY in capitals, separated by" \
         "commas, got '${avail_day}' in '${PACS_AVAILABILITY_DAYS}'" >&2; exit 1 ;;
  esac
done
for avail_var in PACS_AVAILABILITY_START PACS_AVAILABILITY_END; do
  if ! [[ "${!avail_var}" =~ ^(([01][0-9]|2[0-3]):[0-5][0-9]|[0-9]:[0-5][0-9]|24:00)$ ]]; then
    echo "ERROR: ${avail_var} must be a time of day, HH:MM from 00:00 to 23:59 or 24:00 for the end of" \
      "the day, got '${!avail_var}'" >&2
    exit 1
  fi
done
if ! [[ "${PACS_THREADS}" =~ ^[1-9][0-9]{0,8}$ ]]; then
  echo "ERROR: PACS_THREADS must be a whole number from 1, got '${PACS_THREADS}'" >&2
  exit 1
fi
if ! [[ "${PACS_UTILIZATION_PERCENT}" =~ ^([1-9][0-9]?|100)$ ]]; then
  echo "ERROR: PACS_UTILIZATION_PERCENT must be a whole number from 1 to 100, got '${PACS_UTILIZATION_PERCENT}'" >&2
  exit 1
fi

# A time as DQR stores and returns it: H:MM zero-padded, 24:00 as 00:00 (still the end of the day).
# The converge compares the kit with what DQR returns, so the kit's times get the same form.
dqr_time() {
  case "$1" in
    24:00) printf '00:00' ;;
    ?:??) printf '0%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}
avail_start=$(dqr_time "${PACS_AVAILABILITY_START}")
avail_end=$(dqr_time "${PACS_AVAILABILITY_END}")

# DQR (PacsAvailability.isAvailableAtTime) runs a window whose start is not before its end inverted,
# during the day, so refuse one (FLIP#1465). An end of 24:00, stored as 00:00, is the end of the day.
avail_minutes() { printf '%d' $(( 10#${1%%:*} * 60 + 10#${1##*:} )); }  # HH:MM, already validated
avail_end_minutes=1440
[[ "$avail_end" == "00:00" ]] || avail_end_minutes=$(avail_minutes "$avail_end")
if (( $(avail_minutes "$avail_start") >= avail_end_minutes )); then
  echo "ERROR: PACS_AVAILABILITY_START=${PACS_AVAILABILITY_START} must be before" \
    "PACS_AVAILABILITY_END=${PACS_AVAILABILITY_END}: DQR runs a window that crosses midnight inverted, during the" \
    "day (FLIP#1465). For out-of-hours retrieval use 19:00-24:00, 00:00-07:00 or whole days." >&2
  exit 1
fi

# jq parses the /xapi/dicomscp and /xapi/pacs listings below. It ships in the xnat-web image
# (trust/xnat/xnat/Dockerfile), but fail loudly here rather than let a missing binary degrade into a
# silently-empty lookup that would re-register a PACS that already exists.
command -v jq >/dev/null || { echo "ERROR: jq is required by configure-xnat.sh" >&2; exit 1; }

# XNAT_AETITLE against XNAT's own AE-title rule (DicomSCPManager.AE_TITLE_PATTERN in XNAT 1.10): 1 to
# 16 printable ASCII characters, no backslash, neither starting nor ending with a space. XNAT applies
# it when a receiver is created, but not when one is updated in place, and in-place update is how a
# changed XNAT_AETITLE now reaches an existing receiver (FLIP#1456). So the script applies it itself,
# before XNAT is touched. jq rather than [[ =~ ]]: its ranges are code points whatever the locale.
if ! jq -en --arg ae "${XNAT_AETITLE}" \
    '$ae | test("^[!-~]([ -~]{0,14}[!-~])?$") and (contains("\\") | not)' >/dev/null; then
  echo "ERROR: XNAT_AETITLE must be 1 to 16 printable ASCII characters, with no backslash and no" \
    "leading or trailing space (XNAT's own rule for a DICOM AE title), got '${XNAT_AETITLE}'" >&2
  exit 1
fi

wait_for_xnat 900

# NOTE: the plugin-readiness wait deliberately does NOT run here. It cannot: the probe is an
# authenticated plugin route, and an uninitialized XNAT redirects every authenticated route to
# /setup, so it can only answer once the site has been initialized below. See FLIP#966 — running
# it here deadlocked every fresh bring-up against a step downstream of itself.

# Which admin password this XNAT accepts decides the mode. FIRST BOOT: the initial password still
# authenticates — activate with it, then rotate it. CONVERGE: only the configured (rotated) password
# does — an instance a prior run configured, or one just migrated from 1.9, which re-reports itself
# uninitialised; activate with the rotated password and skip the rotation. Everything else is
# re-applied in both modes, which is what lets `upgrade-xnat` redeploy onto a LIVE data dir and
# still land the receiver port, the DQR lockdown, the PACS registration and siteUrl.
# NOTE: when the configured admin password equals the initial one (the dev kits do this), every run
# is a first boot — so the conflict-prone calls below (service account, PACS registration,
# availability intervals) carry their own already-configured guards rather than relying on the mode.
# (These probes check status codes explicitly — a non-200 here is a signal, not an error, so they use
# xnat_request rather than xnat_curl, with their own deadlines. A timeout reads as status 000 and
# reaches the checks below: the wait above proves XNAT serves the login page, not that an
# authenticated route answers.)
#
# On a first boot XNAT serves that login page before its database initialisation finishes, and until
# then both passwords get 401 (FLIP#1384), so the probes are retried a few times. The cap is a count,
# not a deadline: a genuinely wrong password costs one failed login per probe, XNAT locks the account
# after about 20, and a run must fail well short of that. Hence the 1-8 bound below and a single probe
# per round when the two passwords are the same (the dev kits). The Helm xnat-init Job sets one
# attempt: Kubernetes restarts the failed container, which is its retry.
auth_attempts="${XNAT_AUTH_PROBE_ATTEMPTS:-6}"
auth_wait="${XNAT_AUTH_PROBE_WAIT_SECONDS:-20}"
if ! [[ "$auth_attempts" =~ ^[1-8]$ ]]; then
  echo "ERROR: XNAT_AUTH_PROBE_ATTEMPTS must be a whole number from 1 to 8 (each attempt can be a" \
    "failed admin login, and XNAT locks the account after about 20), got '${auth_attempts}'" >&2
  exit 1
fi
# At least a second: a zero wait still spends a failed login per round but gives XNAT no time to
# finish initialising, which is all the retry is for.
if ! [[ "$auth_wait" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: XNAT_AUTH_PROBE_WAIT_SECONDS must be a whole number of seconds from 1, got '${auth_wait}'" >&2
  exit 1
fi
ADMIN_PASSWORD_ROTATED=false
for ((attempt = 1; ; attempt++)); do
  xnat_request 5 15 -o /dev/null -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
    "$XNAT_URL/xapi/siteConfig/initialized"
  init_pw_status=$xnat_status
  [[ "${init_pw_status}" == "200" ]] && break
  if [[ "${XNAT_ADMIN_PASSWORD}" == "${XNAT_ADMIN_INITIAL_PASSWORD}" ]]; then
    rotated_status="${init_pw_status}"
  else
    xnat_request 5 15 -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/siteConfig/initialized"
    rotated_status=$xnat_status
    initialized=$xnat_body
    [[ "${rotated_status}" == "200" ]] && break
  fi
  statuses="initial: HTTP ${init_pw_status:-000}, configured: HTTP ${rotated_status:-000}"
  if [[ "$attempt" -ge "$auth_attempts" ]]; then
    echo "ERROR: neither admin password authenticates (${statuses}) after ${attempt} attempt(s)." >&2
    case "${init_pw_status:-000} ${rotated_status:-000}" in
      *401*)
        echo "  Check XNAT_ADMIN_PASSWORD in the kit against this XNAT's admin account. After about" \
          "20 failed logins XNAT locks the account and rejects even the right password." >&2 ;;
      "000 000")
        echo "  Nothing answered at $XNAT_URL — check that the xnat-web container is running and reachable." >&2 ;;
      *5??*)
        echo "  XNAT returned a server error — check the xnat-web logs." >&2 ;;
    esac
    exit 1
  fi
  echo "Neither admin password authenticates yet (${statuses}); XNAT may still be initialising its" \
    "database. Retrying in ${auth_wait}s (attempt ${attempt}/${auth_attempts})."
  sleep "$auth_wait"
done
if [[ "${init_pw_status}" != "200" ]]; then
  ADMIN_PASSWORD_ROTATED=true
  if [[ "${initialized}" == "true" ]]; then
    echo "XNAT already configured (the initial admin password no longer works) — converging configuration."
  else
    echo "XNAT reports itself uninitialised but only the configured admin password works" \
      "(an instance migrated from an older XNAT) — activating it with that password."
  fi
fi
# The admin password the activation call authenticates with: whichever one XNAT accepts.
if [[ "$ADMIN_PASSWORD_ROTATED" == true ]]; then
  ACTIVATION_PASSWORD="${XNAT_ADMIN_PASSWORD}"
else
  ACTIVATION_PASSWORD="${XNAT_ADMIN_INITIAL_PASSWORD}"
fi

echo "Configuring XNAT instance..."
sleep 10 # Additional wait to ensure XNAT is fully up before proceeding

# Activate XNAT instance. siteUrl must be non-empty on XNAT >= 1.10.0: the Restlet create
# paths (e.g. POST /data/projects) resolve response references through the siteUrl preference
# via URI.create(), which throws an uncaught NPE on null — the entity is created but the
# request returns 500, so imaging-api treats every create as failed. The UI setup wizard
# normally sets this; our headless configure must set it explicitly. Nothing in FLIP consumes
# the rewritten references, so the Docker-internal base URL is the honest default; override
# with XNAT_SITE_URL if an externally reachable URL is ever needed (e.g. for SMTP links).
# Re-issued on converge too: idempotent, and an instance migrated from 1.9.x boots with siteUrl
# empty and re-reports itself uninitialised (every request 302s to /setup) until this lands.
echo "Activating XNAT instance..."
xnat_curl -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${ACTIVATION_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{\"initialized\": true, \"siteUrl\": \"${XNAT_SITE_URL:-$XNAT_URL}\"}"

# Now that the site is initialized, authenticated routes stop redirecting to /setup and the
# plugin-readiness probe can actually answer.
#
# Tomcat serves the login page before plugin routes have registered, so everything below that
# touches a plugin — the DQR settings, the OHIF viewer preferences — would otherwise race into a
# stream of 404s and leave XNAT half-configured. This is the earliest point the wait can succeed,
# and still ahead of every plugin-dependent call. Nothing between here and those calls (password
# rotation, service account, roles, guest) touches a plugin route.
#
# The helper accepts either the initial or already-rotated admin password, so a configuration-only
# retry stays idempotent. XNAT_URL is passed explicitly: it is a plain (unexported) variable here
# and the helper otherwise falls back to its own default, so the two could silently probe and
# configure different hosts.
XNAT_URL="$XNAT_URL" bash wait-for-xnat-plugins.sh

# Change admin password — first boot only: on converge the initial password no longer
# authenticates, and the rotated one is already in place.
if [[ "$ADMIN_PASSWORD_ROTATED" == true ]]; then
  echo "Admin password already rotated — leaving as-is."
else
  echo "Changing admin password..."
  xnat_curl -X PUT "$XNAT_URL/xapi/users/admin" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"${XNAT_ADMIN_USER}\", \"password\": \"${XNAT_ADMIN_PASSWORD}\"}"
fi

# Create service account. Tolerates the 409 XNAT returns when the account
# already exists (re-run on an already-configured instance — see the
# idempotency note above); anything else non-2xx fails the deploy.
echo "Creating service account..."
xnat_request 10 120 -X POST "$XNAT_URL/xapi/users" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"${XNAT_SERVICE_USER}\",
    \"password\": \"${XNAT_SERVICE_PASSWORD}\",
    \"firstName\": \"flip Service Account\",
    \"lastName\": \"flip Service Account\",
    \"email\": \"xnat@example.com\"
  }"
create_user_status=$xnat_status
if [[ "$create_user_status" == 2* ]]; then
  echo "Service account created."
elif [[ "$create_user_status" == "409" ]]; then
  echo "Service account already exists (HTTP 409) — leaving as-is."
else
  echo "ERROR: creating service account failed (HTTP $create_user_status)" >&2
  printf '%s\n' "$xnat_body" >&2
  exit 1
fi

# Add ContainerManager role to admin user
# NOTE This was added when the ContainerManager role was introduced in Container Service 3.7.0 (see
# release notes in https://bitbucket.org/xnatdev/container-service/src/master/CHANGELOG.md) and
# https://wiki.xnat.org/container-service/container-service-administration#ContainerServiceAdministration-EnablingaContainerManager
echo " "
echo "Assigning role 'ContainerManager' to admin account..."
xnat_curl -X PUT "$XNAT_URL/xapi/users/${XNAT_ADMIN_USER}/roles/ContainerManager" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "accept: application/json"

# Assign roles to service account (including 'ContainerManager' role)
echo " "
echo "Assigning roles to service account..."
xnat_curl -X PUT "$XNAT_URL/xapi/users/${XNAT_SERVICE_USER}/groups/" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '[
    "ALL_DATA_ADMIN"
  ]'

xnat_curl -X PUT "$XNAT_URL/xapi/users/${XNAT_SERVICE_USER}/roles/" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '[
    "ContainerManager",
    "DataManager",
    "SiteUser",
    "Administrator",
    "Dqr",
    "non_expiring"
  ]'

# Disable guest account
echo " "
echo "Disabling guest account..."
xnat_curl -X PUT "$XNAT_URL/xapi/users/guest/enabled/false" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}"

# Configure DQR plugin. allowAllUsersToUseDqr stays FALSE (FLIP#846): PACS query/retrieve is
# restricted to site admins and holders of the DQR plugin's "Dqr" role — the service account is
# granted both above, and it is the only account that legitimately drives imports (via imaging-api).
# With the flag true, ANY XNAT account could pull arbitrary studies from the trust PACS.
echo "Configuring DQR plugin..."
xnat_curl -X POST "$XNAT_URL/xapi/dqr/settings" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg ae "${XNAT_AETITLE}" \
        --argjson retry "${DQR_RETRY_WAIT_SECONDS}" --argjson attempts "${DQR_MAX_PACS_REQUEST_ATTEMPTS}" '{
    pacsAvailabilityCheckFrequency: "1 minute",
    dqrWaitToRetryRequestInSeconds: ($retry | tostring),
    assumeSameSessionIfArrivedWithin: "30 minutes",
    allowAllUsersToUseDqr: false,
    dqrCallingAe: $ae,
    notifyAdminOnImport: false,
    allowAllProjectsToUseDqr: true,
    leavePacsAuditTrail: false,
    dqrMaxPacsRequestAttempts: ($attempts | tostring)
  }')"

# Configure site-wide anonymization script
echo "Configuring site-wide anonymization script..."
xnat_curl -X PUT "$XNAT_URL/xapi/anonymize/site" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: text/plain" \
  --data-binary @anon_script.das

# Enable site-wide anonymization
echo "Enabling site-wide anonymization..."
xnat_curl -X PUT "$XNAT_URL/xapi/anonymize/site/enabled" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d 'true'

# The DICOM SCP receiver is converged onto the kit, not re-created (FLIP#1456): this runs on every
# redeploy and `helm upgrade`, and a delete-then-POST left no receiver for the C-STORE return leg of a
# retrieval in flight. FLIP's receiver is kept (the one at XNAT_AETITLE:XNAT_PORT, else the oldest), and:
#   - it is PUT only when a field drifted, because XNAT restarts the port's listener on every PUT;
#   - the PUT is not validated as the POST is, so the AE title is checked at the top of this script, and
#     a receiver is only created by POST (on first boot, after the stock one is deleted);
#   - the other receivers this script owns are deleted first, so the PUT never takes a title and port
#     another receiver holds.
#
# Which receivers this script owns is a property of the receiver itself, not of the port it binds or the
# title it carries: every receiver FLIP creates is stamped with identifier "dqrObjectIdentifier" by the
# POST below, and XNAT's stock receiver — created by the webapp on first boot, always called "XNAT" — is
# the other one it reclaims. `GET /xapi/dicomscp` returns `identifier` per receiver, so both markers are
# readable from the listing.
#
# Matching instead on "our port or our AE title" left an orphan whenever both moved in one change:
# an existing FLIPXNAT:8104 under new config XNAT_AETITLE=FLIPXNAT2 XNAT_PORT=11112 matched neither,
# so the old receiver stayed enabled and bound (FLIP#993).
#
# A receiver an operator registered for some other local DICOM source carries neither marker and is
# left alone — it would otherwise be deleted on every redeploy and `helm upgrade`, with the deletion
# visible only in a Job pod log that is discarded on success.
#
# Both markers are literals in the filters, so nothing operator-supplied is spliced into jq.
response=$(xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" "$XNAT_URL/xapi/dicomscp")

if [[ -z "$response" || "$response" == "[]" ]]; then
    echo "No SCP receivers found."
    response='[]'
else
    # Pre-flight: a receiver carrying neither ownership marker, already bound to the port this
    # deployment is about to register, is refused before anything is written, as the PACS-side guard
    # further down refuses ahead of its deletes. XNAT itself would accept a second AE title on the
    # port; this refusal is also what keeps the unvalidated PUT below off a port another receiver holds.
    squatters=$(printf '%s' "$response" \
      | jq -r --argjson port "${XNAT_PORT}" \
              '.[] | select(.port == $port)
                   | select(.identifier != "dqrObjectIdentifier" and .aeTitle != "XNAT")
                   | "\(.aeTitle):\(.port) (id \(.id))"')
    if [[ -n "$squatters" ]]; then
        echo "ERROR: another DICOM SCP receiver already holds port ${XNAT_PORT}, and it carries" >&2
        echo "       neither FLIP ownership marker (identifier 'dqrObjectIdentifier', AE title" >&2
        echo "       'XNAT'). Refusing to configure this deployment's receiver on a port it would" >&2
        echo "       share with a receiver it does not own. Free the port or remove the receiver in" >&2
        echo "       XNAT's admin UI, or point this deployment at another port via XNAT_PORT. Found:" >&2
        printf '         %s\n' "$squatters" >&2
        exit 1
    fi
fi

# The kit's receiver. dqrObjectIdentifier (the default identifier is not it) is what routes an arriving
# study to the project DQR requested it for, and it is also what marks the receiver as FLIP-owned. The
# body never carries an id: the PUT merges every property of the body into the stored receiver, an id
# included.
scp_payload=$(jq -n --arg ae "${XNAT_AETITLE}" --argjson port "${XNAT_PORT}" '{
    aeTitle: $ae,
    port: $port,
    enabled: true,
    customProcessing: true,
    directArchive: true,
    identifier: "dqrObjectIdentifier",
    anonymizationEnabled: true,
    whitelistEnabled: false,
    routingExpressionsEnabled: false,
    projectRoutingExpression: "",
    subjectRoutingExpression: "",
    sessionRoutingExpression: ""
  }')

# Drift is any field of the payload that differs. A routing expression XNAT returns as null is the
# empty one the kit sends, or a receiver made another way would drift on every run.
# shellcheck disable=SC2016 # jq's variables, not the shell's
scp_jq_defs="$XNAT_JQ_DRIFT"'
def owned: .identifier == "dqrObjectIdentifier" or .aeTitle == "XNAT";
'

scp_keep=$(printf '%s' "$response" | jq -c --arg ae "${XNAT_AETITLE}" --argjson port "${XNAT_PORT}" \
  'map(select(.identifier == "dqrObjectIdentifier")) | sort_by(.id)
   | (map(select(.aeTitle == $ae and .port == $port)) + .) | .[0] // empty')
scp_keep_id=""
if [[ -n "$scp_keep" ]]; then
    scp_keep_id=$(printf '%s' "$scp_keep" | jq -r '.id')
    require_numeric_id "XNAT listed the DICOM SCP receiver" "$scp_keep_id"
fi

stale_ids=$(printf '%s' "$response" | jq -r --argjson keep "${scp_keep_id:-null}" "$scp_jq_defs"'
  .[] | select(owned and .id != $keep) | "\(.id):\(.aeTitle):\(.port)"')

scp_written=false
while IFS=: read -r scp_receiver_id scp_receiver_ae scp_receiver_port; do
    [[ -n "$scp_receiver_id" ]] || continue
    require_numeric_id "XNAT listed a DICOM SCP receiver" "$scp_receiver_id"
    echo "Removing SCP receiver '${scp_receiver_ae}:${scp_receiver_port}' (id ${scp_receiver_id})..."
    xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" \
        -X DELETE "$XNAT_URL/xapi/dicomscp/$scp_receiver_id" >/dev/null
    scp_written=true
done <<< "$stale_ids"

if [[ -z "$scp_keep_id" ]]; then
    echo "Creating SCP receiver '${XNAT_AETITLE}' on port ${XNAT_PORT}..."
    xnat_curl -X POST "$XNAT_URL/xapi/dicomscp" \
      -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
      -H "Content-Type: application/json" \
      -d "$scp_payload"
    scp_written=true
else
    scp_drift=$(jq -cn --argjson entry "$scp_keep" --argjson desired "$scp_payload" \
      "$scp_jq_defs"'drift($entry; $desired)')
    if [[ "$scp_drift" == "[]" ]]; then
        echo "SCP receiver '${XNAT_AETITLE}:${XNAT_PORT}' (id ${scp_keep_id}) already matches the kit" \
             "— leaving as-is."
    else
        echo "SCP receiver id ${scp_keep_id} drifted on $(printf '%s' "$scp_drift" | jq -r 'join(", ")')," \
             "updating it in place to '${XNAT_AETITLE}:${XNAT_PORT}'..."
        xnat_curl -X PUT "$XNAT_URL/xapi/dicomscp/${scp_keep_id}" \
          -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
          -H "Content-Type: application/json" \
          -d "$scp_payload" >/dev/null
        scp_written=true
    fi
fi

# After any write, read the receivers back and fail unless exactly one that this script owns is left,
# at the kit's values, under the id it was kept with. A 200 that persisted nothing is how FLIP#822
# stayed hidden on the PACS side, and the PUT runs none of XNAT's own checks to catch a body it
# misapplied. With nothing written, the listing above already is the read-back.
if [[ "$scp_written" == true ]]; then
    scp_after=$(xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" "$XNAT_URL/xapi/dicomscp")
    scp_problem=$(printf '%s' "${scp_after:-[]}" \
      | jq -r --argjson desired "$scp_payload" --argjson keep "${scp_keep_id:-null}" "$scp_jq_defs"'
          map(select(owned)) as $ours
          | if ($ours | length) != 1 then "\($ours | length) receivers this script owns are left, not one"
            elif $keep != null and $ours[0].id != $keep then "its id changed from \($keep) to \($ours[0].id)"
            else drift($ours[0]; $desired) | select(length > 0) | "it still differs on \(join(", "))"
            end')
    if [[ -n "$scp_problem" ]]; then
        echo "ERROR: the DICOM SCP receiver did not converge on '${XNAT_AETITLE}:${XNAT_PORT}': ${scp_problem}" >&2
        exit 1
    fi
    echo "SCP receiver '${XNAT_AETITLE}:${XNAT_PORT}' is configured."
fi

# Configure OHIF viewer
echo "Configuring OHIF viewer..."
xnat_curl -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d '{"addOhifViewLinkToProjectListingDefaults": true }'

# Register the upstream PACS.
#
# queryRetrievePort is the port XNAT dials the PACS on and must be the port that is actually
# reachable from the XNAT container: the mock Orthanc's fixed container port 4242 over the container
# network, or the trust PACS's real query/retrieve port. The retired ${PACS_DICOM_PORT} variable
# meant the *host-published* port, which is not the same thing, so a kit setting it silently broke
# registration (FLIP#822 / FLIP#862) — hence PACS_QR_PORT is guarded non-empty above and documented
# as the reachable port.
#
# A trust XNAT talks to exactly one PACS, so this script owns the registration list: the configured
# PACS is created or updated in place, and any *other* registration is removed. Two things make that
# the right behaviour rather than merely tidy. A duplicate surfaces as an unspecific 500 (DB
# unique-constraint violation) if we re-POST, and — more importantly — we set
# defaultQueryRetrievePacs on ours, so a leftover entry claiming the same default leaves DQR's
# choice of PACS ambiguous. Changing PACS_AETITLE would otherwise strand the old entry exactly as a
# changed XNAT_AETITLE used to strand the old SCP receiver.
#
# Updating in place rather than delete-and-recreate keeps the PACS id stable, and means a kit change
# actually takes effect: the previous check-then-create silently ignored a drifted host or port on
# redeploy, leaving DQR pointing at the old PACS with no signal to the operator.
# Built with jq rather than string interpolation. Splicing operator-supplied values straight into
# JSON is an injection point: a PACS_HOST of `x", "defaultQueryRetrievePacs": false, "z": "y`
# produces *valid* JSON whose duplicate key wins, silently disabling the flag on the registration
# this script exists to make authoritative; a label containing a quote produces malformed JSON that
# XNAT rejects with an opaque 400 mid-run. --arg/--argjson keep them data. --argjson also parses the
# numeric fields, so a non-numeric port fails here naming the variable, rather than as an
# unexplained 400 (FLIP#993).
pacs_payload=$(jq -n \
  --arg ae "${PACS_AETITLE}" \
  --arg host "${PACS_HOST}" \
  --arg label "${PACS_LABEL}" \
  --argjson port "${PACS_QR_PORT}" \
  --argjson ext "${PACS_SUPPORTS_EXTENDED_NEGOTIATIONS}" \
  '{
    aeTitle: $ae,
    defaultQueryRetrievePacs: true,
    defaultStoragePacs: true,
    host: $host,
    label: $label,
    ormStrategySpringBeanId: "dicomOrmStrategy",
    queryRetrievePort: $port,
    queryable: true,
    storable: true,
    supportsExtendedNegotiations: $ext
  }')

existing_pacs=$(xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/pacs")
pacs_entry=$(printf '%s' "$existing_pacs" | jq -c --arg ae "${PACS_AETITLE}" \
  'map(select(.aeTitle == $ae)) | .[0] // empty')

# Remove every registration that is not the configured one, so exactly one survives. Logged per
# entry: this deletes configuration an administrator may have added through the XNAT UI, and that
# should be visible in the deploy output rather than silent.
#
# Refuse rather than delete when the configuration is still the shipped default. An operator whose
# XNAT already carries a hand-registered real PACS, running an unrelated upgrade without having set
# PACS_AETITLE, would otherwise have that registration deleted and replaced with the mock — a
# retrieval outage caused by a deploy that changed nothing else. Deleting is right only once the
# operator has said which PACS is theirs (FLIP#993).
foreign_pacs=$(printf '%s' "$existing_pacs" \
  | jq -r --arg ae "${PACS_AETITLE}" 'map(select(.aeTitle != $ae)) | .[] | @base64')
if [[ -n "$foreign_pacs" ]]; then
  if [[ "${PACS_AETITLE}" == "ORTHANC" && "${PACS_HOST}" == "orthanc" ]]; then
    echo "ERROR: XNAT has PACS registrations other than the configured one, but PACS_AETITLE and" >&2
    echo "       PACS_HOST are still the mocked-Orthanc defaults. Refusing to delete a registration" >&2
    echo "       this deployment may not own. Set PACS_AETITLE/PACS_HOST to the trust's PACS, or" >&2
    echo "       remove the unwanted registration in XNAT's admin UI. Found:" >&2
    printf '%s\n' "$foreign_pacs" | while read -r entry; do
      [[ -n "$entry" ]] || continue
      printf '         %s\n' "$(printf '%s' "$entry" | base64 -d \
        | jq -r '"\(.aeTitle) at \(.host):\(.queryRetrievePort) (id \(.id))"')" >&2
    done
    exit 1
  fi
  while read -r entry; do
    [[ -n "$entry" ]] || continue
    # base64 per record: an IPv6 host contains colons, which a ':'-delimited read would mis-split.
    stale=$(printf '%s' "$entry" | base64 -d)
    stale_id=$(printf '%s' "$stale" | jq -r '.id')
    echo "Removing PACS $(printf '%s' "$stale" | jq -r '"\(.aeTitle) at \(.host):\(.queryRetrievePort) (id \(.id))"'):" \
         "a trust XNAT retrieves from one PACS, and '${PACS_AETITLE}' is the configured one."
    xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
      -X DELETE "$XNAT_URL/xapi/pacs/${stale_id}" >/dev/null
  done <<< "$foreign_pacs"
fi

if [[ -z "$pacs_entry" ]]; then
  echo "Registering PACS '${PACS_AETITLE}' at ${PACS_HOST}:${PACS_QR_PORT}..."
  xnat_curl -X POST "$XNAT_URL/xapi/pacs" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "$pacs_payload"
else
  PACS_ID=$(printf '%s' "$pacs_entry" | jq -r '.id')

  # Drift is judged over every kit-managed field, not just host+port: label and
  # supportsExtendedNegotiations come from the kit too, and the k8s init job re-runs this script on
  # every helm upgrade against persistent XNAT data — a narrower comparison logs "leaving as-is"
  # and keeps the old flag forever, exactly the silently-ignored kit change the in-place update
  # exists to prevent. Comparison against the desired payload rather than the env vars keeps type
  # handling in jq (port and the flag are JSON number/boolean in both documents, not strings).
  # The two DQR flags are compared too: imaging-api's queries need the default one, and a PACS
  # registered by hand (the admin UI's "Default Q/R AE" starts at No) is adopted without it.
  pacs_drift=$(jq -cn --argjson entry "$pacs_entry" --argjson desired "$pacs_payload" \
    '[("host","queryRetrievePort","label","supportsExtendedNegotiations","defaultQueryRetrievePacs","queryable")
      | select($entry[.] != $desired[.])]')

  if [[ "$pacs_drift" == "[]" ]]; then
    echo "PACS '${PACS_AETITLE}' already registered at ${PACS_HOST}:${PACS_QR_PORT} — leaving as-is."
  else
    echo "PACS '${PACS_AETITLE}' drifted on $(printf '%s' "$pacs_drift" | jq -r 'join(", ")')," \
         "updating to ${PACS_HOST}:${PACS_QR_PORT}..."
    xnat_curl -X PUT "$XNAT_URL/xapi/pacs/${PACS_ID}" \
      -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
      -H "Content-Type: application/json" \
      -d "$pacs_payload"
  fi
fi

# The availability schedule below is written against the registered PACS, so resolve its id whether
# it was just created or already existed.
PACS_ID=$(xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/pacs" \
  | jq -r --arg ae "${PACS_AETITLE}" 'map(select(.aeTitle == $ae and .defaultQueryRetrievePacs and .queryable)) | .[0].id // empty')
: "${PACS_ID:?PACS '${PACS_AETITLE}' is not registered as the DQR default queryable PACS after configuration}"

# Converge the PACS availability schedule (the retrieval throttle, FLIP#993) onto the kit, which owns
# it: one window per listed day. Removals come first (days no longer listed, extra intervals on a
# listed day), then in-place updates of drifted intervals, then creations, so a run that fails
# part-way leaves less retrieval scheduled, never more. A day that matches is not written, and the
# schedule is read back whenever something was. DQR 3.0.0 behaviour this relies on (DqrPacsApi):
#   - PUT {pacsId}/availability/{id} answers 304 when nothing it copies has changed;
#   - PUT takes the day and pacsId from the body, so both are sent (without pacsId the interval would
#     move to PACS 0);
#   - overlap is judged per day, so a create on a day emptied above cannot collide; POST answers 400
#     on overlap, which xnat_curl turns into a failed run.
avail_url="$XNAT_URL/xapi/pacs/${PACS_ID}/availability"
# The plan walks the week, so a day listed twice is scheduled once.
avail_days_json=$(jq -cn '$ARGS.positional' --args "${avail_day_list[@]}")

# The kit's window for one day, as DQR's PacsAvailability takes it.
availability_payload() {  # availability_payload <day>
  jq -cn --arg day "$1" --arg start "$avail_start" --arg end "$avail_end" \
    --argjson pacs "${PACS_ID}" --argjson threads "${PACS_THREADS}" \
    --argjson util "${PACS_UTILIZATION_PERCENT}" '{
      dayOfWeek: $day,
      availabilityStart: $start,
      availabilityEnd: $end,
      threads: $threads,
      utilizationPercent: $util,
      pacsId: $pacs,
      enabled: true
    }'
}

# What to do to each day, as tab-separated lines: action, day, interval id ("-" for a creation, since
# `read` collapses an empty field) and a description for the log, ordered removals, updates,
# creations, unchanged. Drift is judged on the four fields the kit sets. When a listed day has
# several intervals, one matching the kit is kept, otherwise the oldest.
availability_plan() {  # availability_plan <schedule JSON from GET {pacsId}/availability>
  jq -rn --argjson current "$1" --argjson days "$avail_days_json" \
    --arg start "$avail_start" --arg end "$avail_end" \
    --argjson threads "${PACS_THREADS}" --argjson util "${PACS_UTILIZATION_PERCENT}" '
    def window: {availabilityStart, availabilityEnd, threads, utilizationPercent};
    def shown: "\(.availabilityStart)-\(if .availabilityEnd == "00:00" then "24:00" else .availabilityEnd end),"
      + " \(.threads) thread(s), \(.utilizationPercent)%";
    {availabilityStart: $start, availabilityEnd: $end, threads: $threads, utilizationPercent: $util} as $want
    | ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    | [ to_entries[]
        | .key as $order | .value as $day
        | (($current[$day] // []) | sort_by(.id)) as $intervals
        | if any($days[]; . == $day) | not then
            $intervals[] | {rank: 0, $order, op: "remove", $day, id, detail: (window | shown)}
          else
            (first($intervals[] | select(window == $want)) // $intervals[0]) as $keep
            | ( $intervals[] | select(.id != $keep.id)
                | {rank: 0, $order, op: "remove-extra", $day, id, detail: (window | shown)} ),
              ( if $keep == null then
                  {rank: 2, $order, op: "create", $day, id: null, detail: ($want | shown)}
                elif ($keep | window) == $want then
                  {rank: 3, $order, op: "unchanged", $day, id: $keep.id, detail: ($want | shown)}
                else
                  {rank: 1, $order, op: "update", $day, id: $keep.id,
                   detail: (([$want | keys_unsorted[] | select($keep[.] != $want[.])] | join(", "))
                     + " (\($keep | window | shown) -> \($want | shown))")}
                end )
          end ]
    | sort_by(.rank, .order, .id)[]
    | [.op, .day, (.id // "-" | tostring), .detail] | @tsv'
}

# The current schedule, shape-checked so an unexpected answer fails naming the call.
availability_schedule() {
  local schedule
  schedule=$(xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$avail_url") || return 1
  if ! printf '%s' "$schedule" | jq -e 'type == "object" and all(.[]; type == "array")' >/dev/null 2>&1; then
    echo "ERROR: GET $avail_url did not return DQR's schedule (intervals listed by day), got: ${schedule}" >&2
    return 1
  fi
  printf '%s' "$schedule"
}

echo "Converging the PACS availability schedule onto the kit (days ${PACS_AVAILABILITY_DAYS}," \
  "${PACS_AVAILABILITY_START}-${PACS_AVAILABILITY_END}, ${PACS_THREADS} thread(s), ${PACS_UTILIZATION_PERCENT}%)..."
# Two assignments rather than a nested $(...): set -e does not see a failure inside an argument.
avail_current=$(availability_schedule)
avail_plan=$(availability_plan "$avail_current")
if printf '%s\n' "$avail_plan" | grep -qE $'^(update|remove-extra)\t'; then
  echo "  The kit owns this schedule: a change made in XNAT's DQR admin page is replaced below."
fi
avail_wrote=false
while IFS=$'\t' read -r avail_op avail_day avail_id avail_detail; do
  [[ -n "$avail_op" ]] || continue
  [[ "$avail_op" == "create" ]] || require_numeric_id "DQR reported an availability interval for ${avail_day}" "$avail_id"
  case "$avail_op" in
    remove|remove-extra)
      if [[ "$avail_op" == "remove" ]]; then
        echo "  ${avail_day}: removing interval ${avail_id} (${avail_detail}); ${avail_day} is no longer in PACS_AVAILABILITY_DAYS."
      else
        echo "  ${avail_day}: removing extra interval ${avail_id} (${avail_detail}); the kit sets one window per day."
      fi
      xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" -X DELETE "${avail_url}/${avail_id}" >/dev/null
      ;;
    update)
      echo "  ${avail_day}: interval ${avail_id} drifted on ${avail_detail}; updating it in place."
      avail_payload=$(availability_payload "$avail_day")
      xnat_curl --allow-not-modified -X PUT "${avail_url}/${avail_id}" \
        -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
        -H "Content-Type: application/json" \
        -d "$avail_payload" >/dev/null
      ;;
    create)
      echo "  ${avail_day}: no interval scheduled; creating ${avail_detail}."
      avail_payload=$(availability_payload "$avail_day")
      xnat_curl -X POST "$avail_url" \
        -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
        -H "Content-Type: application/json" \
        -d "$avail_payload" >/dev/null
      ;;
    unchanged)
      echo "  ${avail_day}: already ${avail_detail}; leaving as-is."
      continue
      ;;
    *)
      echo "ERROR: unexpected availability action '${avail_op}' for ${avail_day}" >&2
      exit 1
      ;;
  esac
  avail_wrote=true
done <<< "$avail_plan"

# Read back what was written rather than trust each 2xx: XNAT answering 2xx without persisting is
# how the PACS registration bug stayed hidden (FLIP#822).
if [[ "$avail_wrote" == true ]]; then
  avail_current=$(availability_schedule)
  avail_residue=$(availability_plan "$avail_current")
  avail_residue=$(printf '%s\n' "$avail_residue" | grep -v $'^unchanged\t' || true)
  if [[ -n "$avail_residue" ]]; then
    echo "ERROR: after configuration DQR's availability schedule for PACS ${PACS_ID} still differs from the kit." \
      "Outstanding (action, day, interval id, detail):" >&2
    printf '%s\n' "$avail_residue" | sed 's/^/         /' >&2
    exit 1
  fi
fi
echo "PACS availability schedule matches the kit."

# Configure FLIP site branding on the login page: a trust-named welcome description and the FLIP
# FLIP logo (baked into the webapp image at /images/flip-logo.png). XNAT is enclave-only and this is a
# mocked site, so the branding tells users which trust's XNAT they are on. TRUST_NAME is passed
# through from the trust kit (trust/xnat/Makefile exports it); XNAT_SITE_DESCRIPTION overrides the
# whole string. The JSON is built with jq so a trust name containing quotes/apostrophes stays valid.
#
# Applied last, after PACS registration and availability: this is cosmetic, and the script is
# fail-loud by design (every xnat_curl rejection aborts), so it must not sit upstream of the
# registration that data ingestion depends on. A rejected branding write still fails the deploy —
# visibly, at the very end, with nothing left undone before it.
echo "Configuring FLIP site branding..."
site_description="${XNAT_SITE_DESCRIPTION:-}"
if [[ -z "$site_description" && -n "${TRUST_NAME:-}" ]]; then
  site_description="Welcome to FLIP's XNAT at ${TRUST_NAME}"
fi
if [[ -n "$site_description" ]]; then
  branding_json=$(jq -n --arg d "$site_description" \
    '{siteDescriptionType: "Text", siteDescriptionText: $d, siteLogoPath: "/images/flip-logo.png"}')
else
  echo "  No TRUST_NAME/XNAT_SITE_DESCRIPTION set — applying FLIP logo only, leaving description as-is."
  branding_json='{"siteLogoPath": "/images/flip-logo.png"}'
fi
xnat_curl -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$branding_json"

echo "XNAT configuration complete!"
