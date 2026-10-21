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

# shellcheck source=./xnat-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/xnat-lib.sh"

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
# imaging-api gives every project trigger this name, which is how the validation below finds FLIP's.
DCM2NIIX_SUBSCRIPTION_NAME="DICOM-NIfTI Conversion"

case "$XNAT_CS_BACKEND" in
  docker) backend_setting=DATA_PATH ;;
  kubernetes) backend_setting=XNAT_CS_PVC_NAME ;;
  *)
    echo "ERROR: XNAT_CS_BACKEND must be docker or kubernetes, not '$XNAT_CS_BACKEND'" >&2
    exit 1
    ;;
esac
# Checked explicitly, to name the setting this backend needs.
if [[ -z "${!backend_setting:-}" ]]; then
  echo "ERROR: $backend_setting is required for the $XNAT_CS_BACKEND backend" >&2
  exit 1
fi

wait_for_xnat 900

# xnat_curl (xnat-lib.sh) as the admin, which every call here is.
admin_curl() { xnat_curl -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$@"; }

# Name the usual cause of refused Container Service calls up front (TROUBLESHOOTING 2.3a).
ADMIN_ROLES=$(admin_curl "$XNAT_URL/xapi/users/${XNAT_ADMIN_USER}/roles/")
if ! jq -e 'index("ContainerManager")' >/dev/null <<<"$ADMIN_ROLES"; then
  echo "ERROR: ${XNAT_ADMIN_USER} does not hold the ContainerManager role, which configure-xnat.sh grants (TROUBLESHOOTING 2.3a)." >&2
  exit 1
fi

# The backend: Docker through the xnat-socket-proxy sidecar with path translation, or the
# Container Service's Kubernetes backend mounting the xnat-web data PVC into each Job (FLIP#565).
if [[ "$XNAT_CS_BACKEND" == docker ]]; then
  backend_config=$(jq --arg data_path "$DATA_PATH" '.["path-translation-docker-prefix"] = $data_path' container-service-backend-configuration.json)
else
  backend_config=$(jq --arg pvc "$XNAT_CS_PVC_NAME" '.["combined-pvc-name"] = $pvc' container-service-backend-configuration.kubernetes.json)
fi

# Registered again only when a field the config sets has drifted: each POST stores a new server
# under a new id. A GET that fails (no server yet) counts as drift.
xnat_request 10 60 -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/docker/server"
backend_drift=""
if [[ "$xnat_status" == 200 ]] && backend_drift=$(jq -r --argjson desired "$backend_config" \
    "$XNAT_JQ_DRIFT"'drift(.; $desired) | join(", ")' 2>/dev/null <<<"$xnat_body") && [[ -z "$backend_drift" ]]; then
  echo "The $XNAT_CS_BACKEND backend already matches the configuration — leaving it as-is."
else
  echo "Adding the $XNAT_CS_BACKEND backend for the container service${backend_drift:+ (drifted on $backend_drift)}..."
  echo "backend_config: $backend_config"
  # A rejected config fails here: the ping below could still pass against the previous backend.
  admin_curl -X POST "$XNAT_URL/xapi/docker/server" -H "Content-Type: application/json" -d "$backend_config" >/dev/null
fi

# Fail loudly here if the backend is unreachable — the command registration below would still
# "succeed", but every dcm2niix launch would then fail at conversion time.
echo "Pinging the $XNAT_CS_BACKEND backend..."
xnat_request 10 60 -o /dev/null -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}" "$XNAT_URL/xapi/docker/server/ping"
if [[ "$xnat_status" != "200" ]]; then
  echo "ERROR: Container Service cannot reach its $XNAT_CS_BACKEND backend (HTTP $xnat_status)"
  exit 1
fi
echo "Container Service -> $XNAT_CS_BACKEND backend: OK"

# The command body, sent both to register the command and to update it in place. On Kubernetes it
# loses its resource keys, so the pods are BestEffort: they reserved a whole CPU each (FLIP#1448).
DCM2NIIX_COMMAND=$(jq -c --arg backend "$XNAT_CS_BACKEND" \
  'if $backend == "kubernetes" then del(."reserve-memory", ."limit-memory", ."limit-cpu") else . end' \
  dcm2niix_command.json)

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
  HUB_ID=$(admin_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | .[0].id // empty')
  if [[ -z "$HUB_ID" ]]; then
    admin_curl -X POST "$XNAT_URL/xapi/docker/hubs" \
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
EXISTING=$(admin_curl "$XNAT_URL/xapi/commands?name=$DCM2NIIX_NAME")
if [[ $(jq 'length' <<<"$EXISTING") -gt 1 ]]; then
  echo "ERROR: more than one '$DCM2NIIX_NAME' command is registered ($(jq -r '[.[] | "command \(.id) (wrapper \(.xnat[0].id // "none"))"] | join(", ")' <<<"$EXISTING")); expected at most one: delete the one no subscription names, then re-run." >&2
  exit 1
fi
CMD_ID=$(jq -r '.[0].id // empty' <<<"$EXISTING")

if [[ -n "$CMD_ID" ]]; then
  echo "Found existing command ID: $CMD_ID. Updating it in place..."
  admin_curl -X POST "$XNAT_URL/xapi/commands/$CMD_ID" \
    -H "Content-Type: application/json" \
    -d "$DCM2NIIX_COMMAND" >/dev/null
else
  # The POST returns the new id as a bare number, used directly because a re-GET by name once came back empty on prod.
  echo "Command not found. Adding dcm2niix command..."
  CMD_ID=$(admin_curl -X POST "$XNAT_URL/xapi/commands" \
    -H "Content-Type: application/json" \
    -d "$DCM2NIIX_COMMAND")
fi

require_numeric_id "XNAT returned the $DCM2NIIX_NAME command" "$CMD_ID"

# Read it back: the wrapper id is what triggers name, and the image shows the pin landed.
REGISTERED=$(admin_curl "$XNAT_URL/xapi/commands/$CMD_ID")
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
admin_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled" >/dev/null

# Note: this only enables the command site-wide so it can be used per-project.

# ----------------------------------------------------------------
# EVENT SERVICE
# ----------------------------------------------------------------

# Enable Event Service (required for per-project event subscriptions to work)
echo "Enabling event service..."
admin_curl -X PUT "$XNAT_URL/xapi/events/prefs" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' >/dev/null

# Note: We intentionally do NOT create a site-wide event subscription here.
# Per-project event subscriptions are created by the imaging-api during project creation,
# controlled by the dicom_to_nifti flag. This ensures dcm2niix only auto-triggers
# for projects that have opted in to DICOM-to-NIfTI conversion.

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Every admin_curl above aborts the script (via set -e) on a non-2xx
# response, so reaching this block means every write was accepted; these
# re-GETs check the resulting state actually persisted. The wrapper-enabled
# endpoint returns a bare JSON boolean (container-service 3.8.1 —
# CommandConfigurationRestApi.isConfigurationEnabled returns a bare Boolean in
# the 3.8.1 source; re-verified live during the XNAT 1.10.0 upgrade smoke test).
echo " "
echo "Validating dcm2niix setup..."
WRAPPER_ENABLED=$(admin_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$dcm2niix_wrapper_name/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: dcm2niix wrapper is not enabled site-wide (expected true)" >&2
  echo "  body: $WRAPPER_ENABLED" >&2
  exit 1
fi

# Verify the image host for the pinned image's registry persisted (swarm launches NPE without it)
if [[ "$needs_image_host" == true ]]; then
  HUB_COUNT=$(admin_curl "$XNAT_URL/xapi/docker/hubs" \
    | jq -r --arg url "$DCM2NIIX_REGISTRY" 'map(select(.url == $url)) | length')
  if [ "$HUB_COUNT" -lt 1 ]; then
    echo "ERROR: no image host registered with url '$DCM2NIIX_REGISTRY' (expected >= 1)" >&2
    exit 1
  fi
fi

# Verify event service is enabled
EVENT_PREFS=$(admin_curl "$XNAT_URL/xapi/events/prefs")
EVENT_STATUS=$(echo "$EVENT_PREFS" | jq -r '.enabled // empty')
if [ "$EVENT_STATUS" != "true" ]; then
  echo "ERROR: Event service is not enabled (expected true)" >&2
  echo "  body: $EVENT_PREFS" >&2
  exit 1
fi

# A warning, not a failure, so one broken trigger does not block every later deploy.
admin_curl "$XNAT_URL/xapi/events/subscriptions" \
  | jq -r --arg name "$DCM2NIIX_SUBSCRIPTION_NAME" '(. // [])[]
    | select(.name == $name and .valid != true)
    | "WARNING: invalid DICOM->NIfTI trigger: subscription \(.id) (\(.["action-key"])) for project(s) \((.["event-filter"]["project-ids"] // []) | join(", ")): \(.["validation-message"] // "no message")"' >&2

echo " "
echo "✅ dcm2niix configuration complete and validated!"
