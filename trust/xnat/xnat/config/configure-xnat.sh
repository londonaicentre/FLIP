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

# Required, non-empty (":?" also rejects set-but-empty, which `set -u` does
# not): an empty value interpolated into a JSON payload produces invalid JSON
# that XNAT rejects — the failure mode behind the silent PACS registration
# bug (FLIP#822 / FLIP#862).
: "${XNAT_ADMIN_USER:?}" "${XNAT_ADMIN_INITIAL_PASSWORD:?}" "${XNAT_ADMIN_PASSWORD:?}"
: "${XNAT_SERVICE_USER:?}" "${XNAT_SERVICE_PASSWORD:?}" "${XNAT_PORT:?}"

# ${VAR-default} rather than ${VAR:-default} throughout: an *unset* variable takes the default,
# but one set to the empty string stays empty and trips the guard below. An operator who writes
# PACS_HOST= in a kit file must get a loud failure, not a silent fallback to the mocked PACS.
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
: "${PACS_THREADS:?}" "${PACS_UTILIZATION_PERCENT:?}"

# jq parses the /xapi/dicomscp and /xapi/pacs listings below. It ships in the xnat-web image
# (trust/xnat/xnat/Dockerfile), but fail loudly here rather than let a missing binary degrade into a
# silently-empty lookup that would re-register a PACS that already exists.
command -v jq >/dev/null || { echo "ERROR: jq is required by configure-xnat.sh" >&2; exit 1; }

# Wait for XNAT to be available (wall-clock bounded, and each probe carries
# its own timeout, so a dead or wedged XNAT fails the deploy loudly instead
# of printing dots forever — same shape as configure-dcm2niix.sh).
echo "Waiting for XNAT to be available..."
wait_start=$SECONDS
deadline=$((wait_start + 900))
until curl --output /dev/null --silent --head --fail \
  --connect-timeout 5 --max-time 10 "$XNAT_URL/app/template/Login.vm"; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: XNAT did not become available within $((SECONDS - wait_start))s" >&2
    exit 1
  fi
  printf '.'
  sleep 1
done
echo "XNAT is up!"

# NOTE: the plugin-readiness wait deliberately does NOT run here. It cannot: the probe is an
# authenticated plugin route, and an uninitialized XNAT redirects every authenticated route to
# /setup, so it can only answer once the site has been initialized below. See FLIP#966 — running
# it here deadlocked every fresh bring-up against a step downstream of itself.

# Helper: curl wrapper for XNAT's REST API — same contract as xnat_curl in
# configure-dcm2niix.sh, except credentials are passed by the caller: this
# script authenticates its first two calls with the *initial* admin password
# and everything after the rotation with the new one. On 2xx, emits the
# response body on stdout. On any other status — or a curl transport failure —
# reports the request (and, for HTTP failures, the response body) on stderr
# and returns non-zero, which `set -e` turns into an abort at the call site.
# Bare `curl -s` exits 0 on HTTP errors, so a rejected configuration call
# used to skip that step invisibly — silently-unregistered PACS was how the
# empty-PACS_DICOM_PORT bug stayed hidden (FLIP#822 / FLIP#862).
# Unlike the dcm2niix variant, credentials and payloads travel through "$@"
# here, so the args echoed on failure are scrubbed first: the value following
# -u (credentials) or -d/--data-binary (payloads carry the admin and service
# passwords) must never reach the tee'd configure log.
scrub_args() {
  local redact_next=""
  local arg
  local out=""
  for arg in "$@"; do
    if [[ -n "$redact_next" ]]; then
      out+=" <redacted>"
      redact_next=""
    elif [[ "$arg" == "-u" || "$arg" == "-d" || "$arg" == "--data-binary" ]]; then
      out+=" $arg"
      redact_next=1
    else
      out+=" $arg"
    fi
  done
  printf '%s' "${out# }"
}

xnat_curl() {
  local response
  local status
  local body
  local curl_exit=0
  response=$(curl -sS --connect-timeout 10 --max-time 120 -w '\n%{http_code}' "$@") || curl_exit=$?
  if [[ "$curl_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $curl_exit)" >&2
    echo "  args: $(scrub_args "$@")" >&2
    return 1
  fi
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  # Strip CRs so a middlebox emitting \r\n line endings can't make the
  # callers' guards fail on an invisible character (a raw CR is not valid
  # inside JSON strings, so this is lossless).
  status=${status//$'\r'/}
  body=${body//$'\r'/}
  # Fail closed: anything other than a literal 2xx status line (including
  # an empty or non-numeric one) is an error.
  if ! [[ "$status" =~ ^2[0-9]{2}$ ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $(scrub_args "$@")" >&2
    echo "  body: $body" >&2
    return 1
  fi
  if [[ -n "$body" ]]; then
    printf '%s\n' "$body"
  fi
}

# Idempotency short-circuit: if XNAT is already initialized AND the initial
# admin password no longer authenticates, this instance was fully configured
# by a prior run. Re-running the rest of the script would silently 401 on the
# initial-password curls and then produce duplicate-key errors for the service
# account, PACS registration, and PACS availability intervals — so skip it.
# This never suppresses a configuration change on the Swarm path: `up-xnat` wipes XNAT_DATA_DIR on
# every redeploy, so the script always meets a fresh instance — see "Every redeploy is a fresh
# install" in ../../README.md.
# NOTE: when the configured admin password equals the initial one (the dev
# kits do this), this can never fire and the whole script re-runs on every
# `make up-trust` — so the conflict-prone calls below (service account, PACS
# registration, availability intervals) carry their own already-configured
# guards instead of relying on this short-circuit.
# (These probes check status codes explicitly — a non-200 here is a signal,
# not an error, so they intentionally stay bare curl rather than xnat_curl.)
init_pw_status=$(curl -s -o /dev/null -w '%{http_code}' \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
  "$XNAT_URL/xapi/siteConfig/initialized")
if [[ "${init_pw_status}" != "200" ]]; then
  initialized=$(curl -s -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    "$XNAT_URL/xapi/siteConfig/initialized")
  if [[ "${initialized}" == "true" ]]; then
    echo "XNAT already configured (initialized=true, initial password no longer works) — skipping."
    exit 0
  fi
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
echo "Activating XNAT instance..."
xnat_curl -X POST "$XNAT_URL/xapi/siteConfig" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
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

# Change admin password
echo "Changing admin password..."
xnat_curl -X PUT "$XNAT_URL/xapi/users/admin" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_INITIAL_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"${XNAT_ADMIN_USER}\", \"password\": \"${XNAT_ADMIN_PASSWORD}\"}"

# Create service account. Tolerates the 409 XNAT returns when the account
# already exists (re-run on an already-configured instance — see the
# idempotency note above); anything else non-2xx fails the deploy.
echo "Creating service account..."
create_user_status=$(curl -s -o /tmp/create-user-response.json --connect-timeout 10 --max-time 120 \
  -w '%{http_code}' -X POST "$XNAT_URL/xapi/users" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"${XNAT_SERVICE_USER}\",
    \"password\": \"${XNAT_SERVICE_PASSWORD}\",
    \"firstName\": \"flip Service Account\",
    \"lastName\": \"flip Service Account\",
    \"email\": \"xnat@example.com\"
  }") || create_user_status="000"
if [[ "$create_user_status" == 2* ]]; then
  echo "Service account created."
elif [[ "$create_user_status" == "409" ]]; then
  echo "Service account already exists (HTTP 409) — leaving as-is."
else
  echo "ERROR: creating service account failed (HTTP $create_user_status)" >&2
  cat /tmp/create-user-response.json >&2 || true
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
  -d "{
    \"pacsAvailabilityCheckFrequency\": \"1 minute\",
    \"dqrWaitToRetryRequestInSeconds\": \"${DQR_RETRY_WAIT_SECONDS}\",
    \"assumeSameSessionIfArrivedWithin\": \"30 minutes\",
    \"allowAllUsersToUseDqr\": false,
    \"dqrCallingAe\": \"${XNAT_AETITLE}\",
    \"notifyAdminOnImport\": false,
    \"allowAllProjectsToUseDqr\": true,
    \"leavePacsAuditTrail\": false,
    \"dqrMaxPacsRequestAttempts\": \"${DQR_MAX_PACS_REQUEST_ATTEMPTS}\"
  }"

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

# Remove any pre-existing SCP receiver we are about to replace. Two titles matter: XNAT's stock
# default receiver (always "XNAT", created by the webapp on first boot) and the receiver under our
# configured title, so a re-run with changed settings replaces rather than duplicates. When
# XNAT_AETITLE is the default they are the same entry and the loop deduplicates.
response=$(xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" "$XNAT_URL/xapi/dicomscp")

if [[ -z "$response" || "$response" == "[]" ]]; then
    echo "No SCP receivers found."
else
    for ae in $(printf '%s\n' "XNAT" "${XNAT_AETITLE}" | sort -u); do
        # `--arg` keeps the AE title as data rather than splicing it into the filter, so a title
        # containing jq syntax cannot change what is selected.
        scp_receiver_id=$(printf '%s' "$response" | jq -r --arg ae "$ae" \
            'map(select(.aeTitle == $ae)) | .[0].id // empty')

        if [[ -n "$scp_receiver_id" ]]; then
            echo "Removing SCP receiver '$ae' (id $scp_receiver_id)..."
            xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" \
                -X DELETE "$XNAT_URL/xapi/dicomscp/$scp_receiver_id" >/dev/null
        else
            echo "No SCP receiver with aeTitle='$ae' found."
        fi
    done
fi

# Configure SCP receiver to have dqrObjectIdentifier as the identifier (the default is not)
echo "Configuring SCP receiver '${XNAT_AETITLE}' on port ${XNAT_PORT}..."
xnat_curl -X POST "$XNAT_URL/xapi/dicomscp" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"aeTitle\": \"${XNAT_AETITLE}\",
    \"port\": ${XNAT_PORT},
    \"enabled\": true,
    \"customProcessing\": true,
    \"directArchive\": true,
    \"identifier\": \"dqrObjectIdentifier\",
    \"anonymizationEnabled\": true,
    \"whitelistEnabled\": false,
    \"whitelistText\": \"\",
    \"routingExpressionsEnabled\": false,
    \"projectRoutingExpression\": \"\",
    \"subjectRoutingExpression\": \"\",
    \"sessionRoutingExpression\": \"\"
  }"

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
# A duplicate registration surfaces as an unspecific 500 (DB unique-constraint violation), so
# idempotency is a lookup by aeTitle. Unlike the previous check-then-create, an existing entry whose
# host or port has drifted from the configured values is *updated*: leaving it untouched meant a kit
# change was silently ignored on redeploy, and the operator had no signal that DQR was still
# pointing at the old PACS.
pacs_payload="{
      \"aeTitle\": \"${PACS_AETITLE}\",
      \"defaultQueryRetrievePacs\": true,
      \"defaultStoragePacs\": true,
      \"host\": \"${PACS_HOST}\",
      \"label\": \"${PACS_LABEL}\",
      \"ormStrategySpringBeanId\": \"dicomOrmStrategy\",
      \"queryRetrievePort\": ${PACS_QR_PORT},
      \"queryable\": true,
      \"storable\": true,
      \"supportsExtendedNegotiations\": true
    }"

existing_pacs=$(xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/pacs")
pacs_entry=$(printf '%s' "$existing_pacs" | jq -c --arg ae "${PACS_AETITLE}" \
  'map(select(.aeTitle == $ae)) | .[0] // empty')

if [[ -z "$pacs_entry" ]]; then
  echo "Registering PACS '${PACS_AETITLE}' at ${PACS_HOST}:${PACS_QR_PORT}..."
  xnat_curl -X POST "$XNAT_URL/xapi/pacs" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "$pacs_payload"
else
  PACS_ID=$(printf '%s' "$pacs_entry" | jq -r '.id')
  current_host=$(printf '%s' "$pacs_entry" | jq -r '.host // empty')
  current_port=$(printf '%s' "$pacs_entry" | jq -r '.queryRetrievePort // empty')

  if [[ "$current_host" == "${PACS_HOST}" && "$current_port" == "${PACS_QR_PORT}" ]]; then
    echo "PACS '${PACS_AETITLE}' already registered at ${PACS_HOST}:${PACS_QR_PORT} — leaving as-is."
  else
    echo "PACS '${PACS_AETITLE}' registered at ${current_host}:${current_port}," \
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
  | jq -r --arg ae "${PACS_AETITLE}" 'map(select(.aeTitle == $ae)) | .[0].id // empty')
: "${PACS_ID:?PACS '${PACS_AETITLE}' is not registered after configuration}"

# Configure the PACS availability schedule — the throttle for a production PACS, which may refuse
# further associations after a certain volume, and which a trust may want restricted to out-of-hours
# (FLIP#993). Defaults are all week, all day, one thread.
#
# DQR appears to pre-create availability intervals when the PACS is registered: on XNAT 1.10 +
# DQR 3.0.0 this POST returns 400 "probable overlap with existing interval" for an already-scheduled
# day, so 400 is treated as "already configured" rather than a failure. Anything else non-2xx is a
# real error and fails the deploy.
for DAY in ${PACS_AVAILABILITY_DAYS//,/ }; do
  echo "Setting PACS availability for $DAY..."
  avail_body=/tmp/pacs-availability-response.json
  avail_status=$(curl -s -o "$avail_body" --connect-timeout 10 --max-time 120 -w '%{http_code}' \
    -X POST "$XNAT_URL/xapi/pacs/${PACS_ID}/availability" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{
      \"availabilityEnd\": \"${PACS_AVAILABILITY_END}\",
      \"availabilityStart\": \"${PACS_AVAILABILITY_START}\",
      \"availableNow\": true,
      \"dayOfWeek\": \"$DAY\",
      \"enabled\": true,
      \"pacsId\": ${PACS_ID},
      \"threads\": ${PACS_THREADS},
      \"utilizationPercent\": ${PACS_UTILIZATION_PERCENT}
    }") || avail_status="000"
  if [[ "$avail_status" == 2* ]]; then
    continue
  elif [[ "$avail_status" == "400" ]]; then
    echo "  Availability interval for $DAY already exists (HTTP 400 overlap) — leaving as-is."
  else
    echo "ERROR: setting PACS availability for $DAY failed (HTTP $avail_status)" >&2
    cat "$avail_body" >&2 || true
    exit 1
  fi
done

echo "XNAT configuration complete!"
