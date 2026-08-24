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

# The below are fixed values for now
XNAT_URL="http://xnat-web:8080" # internal to Docker network
ORTHANC_HOST="orthanc" # name of the service (container) in docker-compose
ORTHANC_AETITLE="ORTHANC"

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
  -d '{
    "pacsAvailabilityCheckFrequency": "1 minute",
    "dqrWaitToRetryRequestInSeconds": "300",
    "assumeSameSessionIfArrivedWithin": "30 minutes",
    "allowAllUsersToUseDqr": false,
    "dqrCallingAe": "XNAT",
    "notifyAdminOnImport": false,
    "allowAllProjectsToUseDqr": true,
    "leavePacsAuditTrail": false,
    "dqrMaxPacsRequestAttempts": "100"
  }'

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

# Get SCP receivers
response=$(xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" "$XNAT_URL/xapi/dicomscp")

# Debug: Print Raw Response
echo "Raw API Response: $response"

# Check if response is empty or invalid
if [[ -z "$response" || "$response" == "[]" ]]; then
    echo "No SCP receivers found."
else
    # Extract the SCP receiver ID with "aeTitle": "XNAT" using grep and sed
    scp_receiver_data=$(echo "$response" | grep -o '{[^}]*"aeTitle"[^}]*}' | grep '"aeTitle":"XNAT"')
    echo "SCP Receiver Data: $scp_receiver_data"

    if [[ -n "$scp_receiver_data" ]]; then
        # Extract the "id" field of the SCP receiver
        scp_receiver_id=$(echo "$scp_receiver_data" | sed -n 's/.*"id":\([0-9]\+\).*/\1/p')

        if [[ -n "$scp_receiver_id" ]]; then
            echo "Removing SCP Receiver with ID: $scp_receiver_id..."

            # Send DELETE request to remove the SCP receiver
            delete_response=$(xnat_curl -u "$XNAT_ADMIN_USER:$XNAT_ADMIN_PASSWORD" -X DELETE "$XNAT_URL/xapi/dicomscp/$scp_receiver_id")

            echo "Delete Response: $delete_response"
            echo "SCP Receiver removed successfully."
        else
            echo "Failed to extract SCP receiver ID."
        fi
    else
        echo "No SCP receiver with aeTitle='XNAT' found."
    fi
fi

# Configure SCP receiver to have dqrObjectIdentifier as the identifier (the default is not)
echo "Configuring SCP receiver..."
xnat_curl -X POST "$XNAT_URL/xapi/dicomscp" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"aeTitle\": \"XNAT\",
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

# Register PACS
# queryRetrievePort is the port XNAT dials Orthanc on over the Docker network
# (the "host" below is the orthanc service name), so Orthanc's fixed container
# port 4242 is the only correct value. The old ${PACS_DICOM_PORT} indirection
# was nominally the *host-published* DICOM port — its "${PACS_DICOM_PORT}:4242"
# mappings are commented out in every compose file — so a kit setting it to
# anything but 4242 silently broke registration (FLIP#822 / FLIP#862).
# Check-then-create: a duplicate registration surfaces as an unspecific 500
# (DB unique-constraint violation), so re-run idempotency is a lookup by
# aeTitle rather than a tolerated status code.
existing_pacs=$(xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/pacs")
if printf '%s' "$existing_pacs" | grep -q "\"aeTitle\":\"${ORTHANC_AETITLE}\""; then
  echo "PACS '${ORTHANC_AETITLE}' already registered — leaving as-is."
else
  echo "Registering PACS..."
  xnat_curl -X POST "$XNAT_URL/xapi/pacs" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{
      \"aeTitle\": \"${ORTHANC_AETITLE}\",
      \"defaultQueryRetrievePacs\": true,
      \"defaultStoragePacs\": true,
      \"host\": \"${ORTHANC_HOST}\",
      \"label\": \"Test PACS instance\",
      \"ormStrategySpringBeanId\": \"dicomOrmStrategy\",
      \"queryRetrievePort\": 4242,
      \"queryable\": true,
      \"storable\": true,
      \"supportsExtendedNegotiations\": true
    }"
fi

# Configure PACS availability schedule (all days). DQR appears to pre-create
# availability intervals when the PACS is registered: on XNAT 1.10 + DQR 3.0.0
# this POST returns 400 "probable overlap with existing interval" for an
# already-scheduled day, so 400 is treated as "already configured" rather than
# a failure. Anything else non-2xx is a real error and fails the deploy.
for DAY in MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY; do
  echo "Setting PACS availability for $DAY..."
  avail_body=/tmp/pacs-availability-response.json
  avail_status=$(curl -s -o "$avail_body" --connect-timeout 10 --max-time 120 -w '%{http_code}' \
    -X POST "$XNAT_URL/xapi/pacs/1/availability" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{
      \"availabilityEnd\": \"24:00\",
      \"availabilityStart\": \"00:00\",
      \"availableNow\": true,
      \"dayOfWeek\": \"$DAY\",
      \"enabled\": true,
      \"pacsId\": 1,
      \"threads\": 1,
      \"utilizationPercent\": 100
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
