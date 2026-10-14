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

# Exit on first error, undefined variable, or pipe failure.
set -euo pipefail

# This script configures the dcm2niix container service in XNAT, for Compose (inside xnat-web) and
# for the Helm chart's xnat-init Job, which runs this same file from the xnat-web image.
# It is intended to be run after configure-xnat.sh (which changes the admin password).
#
# Required environment variables:
#   XNAT_ADMIN_USER     - XNAT admin username
#   XNAT_ADMIN_PASSWORD - XNAT admin password (must match what configure-xnat.sh set)
#   XNAT_CS_BACKEND     - docker (default, Compose) or kubernetes (the chart)
#   DATA_PATH           - docker: host path for Docker path translation
#   XNAT_CS_PVC_NAME    - kubernetes: the xnat-web data PVC the conversion Jobs mount
#

XNAT_URL="${XNAT_URL:-http://xnat-web:8080}" # internal to the Docker network or the cluster
XNAT_CS_BACKEND="${XNAT_CS_BACKEND:-docker}"
DCM2NIIX_NAME="dcm2niix"
# imaging-api gives every project trigger this name, which is how the repair below finds FLIP's.
DCM2NIIX_SUBSCRIPTION_NAME="DICOM-NIfTI Conversion"

case "$XNAT_CS_BACKEND" in
  docker) backend_setting=DATA_PATH ;;
  kubernetes) backend_setting=XNAT_CS_PVC_NAME ;;
  *)
    echo "ERROR: XNAT_CS_BACKEND must be docker or kubernetes, not '$XNAT_CS_BACKEND'" >&2
    exit 1
    ;;
esac
# Checked explicitly: under the EXIT trap below, bash 3.2 exits 0 after a ${VAR:?} error.
if [[ -z "${!backend_setting:-}" ]]; then
  echo "ERROR: $backend_setting is required for the $XNAT_CS_BACKEND backend" >&2
  exit 1
fi

# XNAT's listings go through files: Linux caps one command-line argument at 128 KiB.
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Wait for XNAT to be available (wall-clock bounded, and each probe carries
# its own timeout, so a dead or wedged XNAT fails the deploy loudly instead
# of printing dots forever).
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

# Helper: curl wrapper for XNAT's REST API. On 2xx, emits the response body
# on stdout for the caller to capture. On any other status — or a curl
# transport failure — reports the request (and, for HTTP failures, the
# response body) on stderr and returns non-zero, which the script's
# `set -e` turns into an abort at the call site (so don't call this inside
# `if`/`||` without handling the failure). Use it for every request to
# XNAT's REST API, reads included: an unnoticed bad GET response is how the
# original empty-CMD_ID deploy failure slipped through — bare `curl -s`
# surfaced neither HTTP errors nor unexpected bodies.
xnat_curl() {
  local response
  local status
  local body
  local curl_exit=0
  response=$(curl -sS --connect-timeout 10 --max-time 120 -w '\n%{http_code}' "$@" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || curl_exit=$?
  if [[ "$curl_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $curl_exit)" >&2
    echo "  args: $*" >&2
    return 1
  fi
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  # Strip CRs so a middlebox emitting \r\n line endings can't make the
  # callers' numeric/boolean guards fail on an invisible character (a raw
  # CR is not valid inside JSON strings, so this is lossless).
  status=${status//$'\r'/}
  body=${body//$'\r'/}
  # Fail closed: anything other than a literal 2xx status line (including
  # an empty or non-numeric one) is an error.
  if ! [[ "$status" =~ ^2[0-9]{2}$ ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $*" >&2
    echo "  body: $body" >&2
    return 1
  fi
  printf '%s' "$body"
}

# Name the usual cause of refused Container Service calls up front (TROUBLESHOOTING 2.3a).
ADMIN_ROLES=$(xnat_curl "$XNAT_URL/xapi/users/${XNAT_ADMIN_USER}/roles/")
if ! jq -e 'index("ContainerManager")' >/dev/null <<<"$ADMIN_ROLES"; then
  echo "ERROR: ${XNAT_ADMIN_USER} does not hold the ContainerManager role, which configure-xnat.sh grants (TROUBLESHOOTING 2.3a)." >&2
  exit 1
fi

# The backend: Docker through the xnat-socket-proxy sidecar with path translation, or the
# Container Service's Kubernetes backend mounting the xnat-web data PVC into each Job (FLIP#565).
echo "Adding the $XNAT_CS_BACKEND backend for the container service..."
if [[ "$XNAT_CS_BACKEND" == docker ]]; then
  backend_config=$(jq --arg data_path "$DATA_PATH" '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)
else
  backend_config=$(jq --arg pvc "$XNAT_CS_PVC_NAME" '.["combined-pvc-name"] = $pvc' container-service-backend-configuration.kubernetes.json)
fi

echo "backend_config: $backend_config"
# curl exits 0 on HTTP 4xx/5xx (no --fail), so check the status explicitly — if XNAT
# rejects the new backend config, the ping below could still pass against a previously
# registered backend and mask the failure. "000" = transport error / timeout.
post_status=$(curl -s -o /tmp/docker-server-post.json --max-time 60 -w "%{http_code}" \
  -X POST "$XNAT_URL/xapi/docker/server" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "$backend_config") || post_status="000"
if [[ "$post_status" != 2* ]]; then
  echo "ERROR: POST /xapi/docker/server returned HTTP $post_status"
  cat /tmp/docker-server-post.json 2>/dev/null || true
  exit 1
fi

# Fail loudly here if the backend is unreachable — the command registration below would still
# "succeed", but every dcm2niix launch would then fail at conversion time.
echo "Pinging the $XNAT_CS_BACKEND backend..."
ping_status=$(curl -s -o /dev/null --max-time 60 -w "%{http_code}" "$XNAT_URL/xapi/docker/server/ping" \
  -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || ping_status="000"
if [[ "$ping_status" != "200" ]]; then
  echo "ERROR: Container Service cannot reach its $XNAT_CS_BACKEND backend (HTTP $ping_status)"
  exit 1
fi
echo "Container Service -> $XNAT_CS_BACKEND backend: OK"

# The command body, sent both to register the command and to update it in place.
DCM2NIIX_COMMAND_JSON=dcm2niix_command.json
if [[ "$XNAT_CS_BACKEND" == kubernetes ]]; then
  # Without resource keys the pods are BestEffort; on Kubernetes they reserved a whole CPU each (FLIP#1448).
  DCM2NIIX_COMMAND_JSON="$WORK_DIR/dcm2niix_command.json"
  jq 'del(."reserve-memory", ."limit-memory", ."limit-cpu")' dcm2niix_command.json >"$DCM2NIIX_COMMAND_JSON"
fi

# ----------------------------------------------------------------
# IMAGE HOST (registry entry for the pinned dcm2niix image)
# ----------------------------------------------------------------

# In swarm mode the Container Service resolves registry credentials for `docker service create` by
# looking up an image-host entry whose `url` equals the registry hostname parsed from the image
# string — the BARE hostname, no scheme (container-service 3.8.1, DockerControlApi.authConfig →
# dockerHubService.getByUrl). With no matching entry the resolved AuthConfig is null, and the swarm
# launch path passes it to docker-java's withAuthConfig unguarded (unlike the pull path, which
# null-checks), so every launch dies with `NullPointerException: authConfig was not specified`
# before a service exists — even for a public image that needs no credentials. Register the
# registry as a credential-less image host so the lookup resolves. The hostname is derived from the
# image pinned in dcm2niix_command.json so it can never drift from the image actually launched; an
# image with no registry component is a Docker Hub image, covered by the built-in default entry.
# Kubernetes pulls through kubelet, so it needs no entry.
DCM2NIIX_IMAGE=$(jq -r '.image // empty' dcm2niix_command.json)
DCM2NIIX_REGISTRY=${DCM2NIIX_IMAGE%%/*}
needs_image_host=false
if [[ "$XNAT_CS_BACKEND" == docker ]] \
  && [[ "$DCM2NIIX_REGISTRY" == *.* || "$DCM2NIIX_REGISTRY" == *:* || "$DCM2NIIX_REGISTRY" == localhost ]]; then
  needs_image_host=true
fi
if [[ "$needs_image_host" == true ]]; then
  echo "Ensuring image host '$DCM2NIIX_REGISTRY' is registered..."
  HUB_ID=$(xnat_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | .[0].id // empty')
  if [[ -z "$HUB_ID" ]]; then
    xnat_curl -X POST "$XNAT_URL/xapi/docker/hubs" \
      -H "Content-Type: application/json" \
      -d "{\"name\": \"$DCM2NIIX_REGISTRY\", \"url\": \"$DCM2NIIX_REGISTRY\"}" >/dev/null
    echo "Image host '$DCM2NIIX_REGISTRY' registered."
  else
    echo "Image host '$DCM2NIIX_REGISTRY' already registered (id=$HUB_ID)."
  fi
else
  echo "No image host needed (backend $XNAT_CS_BACKEND, registry '$DCM2NIIX_REGISTRY')."
fi

# ----------------------------------------------------------------
# CONTAINER SERVICE
# ----------------------------------------------------------------

dcm2niix_wrapper_name=$(jq -r '.xnat[0].name // empty' dcm2niix_command.json)
if [[ -z "$dcm2niix_wrapper_name" ]]; then
  echo "ERROR: no .xnat[0].name wrapper in dcm2niix_command.json." >&2
  exit 1
fi

# Update the command in place, so its wrapper keeps the id every project trigger names (FLIP#1451).
echo "Checking if $DCM2NIIX_NAME command exists..."
EXISTING=$(xnat_curl "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME")
if [[ $(jq 'length' <<<"$EXISTING") -gt 1 ]]; then
  echo "ERROR: more than one '$DCM2NIIX_NAME' command is registered ($(jq -r '[.[] | "command \(.id) (wrapper \(.xnat[0].id // "none"))"] | join(", ")' <<<"$EXISTING")); expected at most one: delete the one no subscription names, then re-run." >&2
  exit 1
fi
CMD_ID=$(jq -r '.[0].id // empty' <<<"$EXISTING")

if [[ -n "$CMD_ID" ]]; then
  echo "Found existing command ID: $CMD_ID. Updating it in place..."
  xnat_curl -X POST "$XNAT_URL/xapi/commands/$CMD_ID" \
    -H "Content-Type: application/json" \
    -d @"$DCM2NIIX_COMMAND_JSON" >/dev/null
else
  # The POST returns the new id as a bare number, used directly because a re-GET by name once came back empty on prod.
  echo "Command not found. Adding dcm2niix command..."
  CMD_ID=$(xnat_curl -X POST "$XNAT_URL/xapi/commands" \
    -H "Content-Type: application/json" \
    -d @"$DCM2NIIX_COMMAND_JSON")
fi

if ! [[ "$CMD_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: XNAT did not give a numeric $DCM2NIIX_NAME command id." >&2
  echo "  body: $CMD_ID" >&2
  exit 1
fi

# Read it back: the wrapper id is what triggers name, and the image shows the pin landed.
REGISTERED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID")
WRAPPER_ID=$(jq -r --arg w "$dcm2niix_wrapper_name" '[.xnat[]? | select(.name == $w) | .id][0] // empty' <<<"$REGISTERED")
REGISTERED_IMAGE=$(jq -r '.image // empty' <<<"$REGISTERED")
if [[ -z "$WRAPPER_ID" ]]; then
  echo "ERROR: command $CMD_ID has no '$dcm2niix_wrapper_name' wrapper." >&2
  exit 1
fi
if [[ "$REGISTERED_IMAGE" != "$DCM2NIIX_IMAGE" ]]; then
  echo "ERROR: command $CMD_ID runs '$REGISTERED_IMAGE', not the pinned '$DCM2NIIX_IMAGE'." >&2
  exit 1
fi

echo "Command ID: $CMD_ID"
echo "Wrapper Name: $dcm2niix_wrapper_name (id $WRAPPER_ID)"

# Enable the dcm2niix command at the site level (makes it available for per-project use)
# See https://wiki.xnat.org/container-service/container-service-api for more details
echo "Enabling $DCM2NIIX_NAME command..."
xnat_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null

# Note: this only enables the command site-wide so it can be used per-project.

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service (required for per-project event subscriptions to work)
echo "Enabling event service..."
xnat_curl -X PUT "$XNAT_URL/xapi/events/prefs" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' >/dev/null

# Note: We intentionally do NOT create a site-wide event subscription here.
# Per-project event subscriptions are created by the imaging-api during project creation,
# controlled by the dicom_to_nifti flag. This ensures dcm2niix only auto-triggers
# for projects that have opted in to DICOM-to-NIfTI conversion.

# This script deletes no event subscription: the cleanup that used to stand here deleted every project's.

SUBS_FILE="$WORK_DIR/subscriptions.json"
PROJECTS_FILE="$WORK_DIR/projects.json"
ACTIONS_FILE="$WORK_DIR/actions.json"
xnat_curl "$XNAT_URL/xapi/events/subscriptions" >"$SUBS_FILE"
xnat_curl "$XNAT_URL/data/projects?format=json" >"$PROJECTS_FILE"
xnat_curl "$XNAT_URL/xapi/events/allactions" >"$ACTIONS_FILE"

# The action key XNAT offers for this wrapper, rather than one built here.
DCM2NIIX_ACTION=$(jq -r --arg w "$dcm2niix_wrapper_name" --arg id "$WRAPPER_ID" '
  [.[] | select(.["display-name"] == $w and (.["action-key"] | split(":") | last) == $id) | .["action-key"]]
  | if length == 1 then .[0] else empty end' <"$ACTIONS_FILE")
if [[ -z "$DCM2NIIX_ACTION" ]]; then
  echo "ERROR: XNAT lists no single event-service action for wrapper '$dcm2niix_wrapper_name' (id $WRAPPER_ID)." >&2
  exit 1
fi

# Re-point FLIP's triggers whose action no longer exists (an earlier configure deleted the wrapper),
# enabling the wrapper for the project first because XNAT validates the update; a refusal only warns.
echo "Repairing project DICOM->NIfTI triggers left on a deleted wrapper..."
STALE_SUBS=$(jq -c --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" \
  --slurpfile actions "$ACTIONS_FILE" --slurpfile projects "$PROJECTS_FILE" '
  [$actions[0][]["action-key"]] as $live
  | [$projects[0].ResultSet.Result[].ID] as $known
  | (. // [])[]
  | select(.name == $name and (.["action-key"] as $key | $live | index($key) | not))
  | select((.["event-filter"]["project-ids"] // []) | length > 0)
  | . + {missing: [.["event-filter"]["project-ids"][] | select(. as $p | $known | index($p) | not)]}' <"$SUBS_FILE")
REPAIRED=0
REPAIR_FAILED=0
while IFS= read -r sub; do
  [[ -n "$sub" ]] || continue
  sub_id=$(jq -r '.id' <<<"$sub")
  projects=$(jq -r '.["event-filter"]["project-ids"] | join(" ")' <<<"$sub")
  if [[ -n $(jq -r '.missing[]' <<<"$sub") ]]; then
    echo "Skipped subscription $sub_id: its project is no longer in XNAT ($projects)."
    continue
  fi
  update=$(jq -c --arg key "$DCM2NIIX_ACTION" \
    '{"action-key": $key, "active": .active, "act-as-event-user": (.["act-as-event-user"] // false), "attributes": (.attributes // {})}' <<<"$sub")
  ok=true
  for project in $projects; do
    xnat_curl -X PUT "$XNAT_URL/xapi/projects/$project/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null || ok=false
  done
  if [[ "$ok" == true ]]; then
    xnat_curl -X PUT "$XNAT_URL/xapi/events/subscription/$sub_id" -H "Content-Type: application/json" -d "$update" >/dev/null || ok=false
  fi
  if [[ "$ok" == true ]]; then
    echo "Repaired the DICOM->NIfTI trigger of project $projects: subscription $sub_id now runs wrapper $WRAPPER_ID."
    REPAIRED=$((REPAIRED + 1))
  else
    echo "WARNING: could not repair the DICOM->NIfTI trigger of project(s) $projects (subscription $sub_id); see the error above." >&2
    REPAIR_FAILED=$((REPAIR_FAILED + 1))
  fi
done <<<"$STALE_SUBS"
echo "Project triggers repaired: $REPAIRED; could not repair: $REPAIR_FAILED."

# FLIP projects (a hub-id secondary_ID) with no trigger at all are named, not recreated: whether one
# converts is the project's hub-side dicom_to_nifti setting (restore-nifti-triggers, trust/xnat/README.md).
jq -r --slurpfile subs "$SUBS_FILE" --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" '
  [($subs[0] // [])[] | select(.name == $name) | (.["event-filter"]["project-ids"] // [])[]] as $covered
  | .ResultSet.Result[]
  | select((.secondary_ID // "") | test("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"))
  | select(.ID as $id | ($covered | index($id)) == null)
  | "WARNING: project \(.ID) (hub project \(.secondary_ID)) has no DICOM->NIfTI trigger, so its scans are not converted. Restore it with restore-nifti-triggers (trust/xnat/README.md)."' \
  <"$PROJECTS_FILE" >&2

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Every xnat_curl above but a trigger repair aborts the script (via set -e) on a non-2xx
# response, so reaching this block means every write was accepted; these
# re-GETs check the resulting state actually persisted. The wrapper-enabled
# endpoint returns a bare JSON boolean (container-service 3.8.1 —
# CommandConfigurationRestApi.isConfigurationEnabled returns a bare Boolean in
# the 3.8.1 source; re-verified live during the XNAT 1.10.0 upgrade smoke test).
echo " "
echo "Validating dcm2niix setup..."
WRAPPER_ENABLED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: dcm2niix wrapper is not enabled site-wide (expected true)" >&2
  echo "  body: $WRAPPER_ENABLED" >&2
  exit 1
fi

# Verify the image host for the pinned image's registry persisted (swarm launches NPE without it)
if [[ "$needs_image_host" == true ]]; then
  HUB_COUNT=$(xnat_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | length')
  if [ "$HUB_COUNT" -lt 1 ]; then
    echo "ERROR: no image host registered with url '$DCM2NIIX_REGISTRY' (expected >= 1)" >&2
    exit 1
  fi
fi

# Verify event service is enabled
EVENT_PREFS=$(xnat_curl "$XNAT_URL/xapi/events/prefs")
EVENT_STATUS=$(echo "$EVENT_PREFS" | jq -r '.enabled // empty')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true)" >&2
  echo "  body: $EVENT_PREFS" >&2
  exit 1
fi

# A warning, not a failure, so one broken trigger does not block every later deploy.
xnat_curl "$XNAT_URL/xapi/events/subscriptions" \
  | jq -r --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" '(. // [])[]
    | select(.name == $name and .valid != true)
    | "WARNING: invalid DICOM->NIfTI trigger: subscription \(.id) (\(.["action-key"])) for project(s) \((.["event-filter"]["project-ids"] // []) | join(", ")): \(.["validation-message"] // "no message")"' >&2

echo " "
echo "✅ dcm2niix configuration complete and validated!"
