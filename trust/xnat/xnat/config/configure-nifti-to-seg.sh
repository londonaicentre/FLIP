#!/bin/bash
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Registers the nifti_to_seg container-service command and its event subscription (FLIP#1945).
# Run only on trusts that set NIFTI_TO_SEG=true: `make xnat-configure` gates it (xnat-configure-nifti-to-seg
# in trust/xnat/Makefile).
#
# An FL job (an `inference` job, say) stores its masks on a scan with flip.add_mask, as NIfTI,
# which the XNAT OHIF viewer cannot draw. This command turns them into a DICOM-SEG ROI collection on
# the session, which it can. The trigger is the resource itself. imaging-api uploads one file per
# request, so the job's mask creates the resource (a ResourceEvent CREATED) and the labels.json it writes
# last updates it (UPDATED): subscribing to UPDATED alone converts each mask once, with both files in
# place. A CREATED subscription as well would launch a second container that races the first to the
# same ROI collection, because labels.json has landed too by the time it starts. The subscription is site-wide
# and filtered on the resource label, so any project's job can use it. It runs the command as the user who
# caused the event (act-as-event-user), not as the admin running this script: imaging-api's service account
# for a job's upload, the person themselves for a hand upload.
#
# Must run AFTER configure-dcm2niix.sh, which registers the Docker backend and verifies the
# socket proxy.
#
# Required environment variables:
#   XNAT_ADMIN_USER     - XNAT admin username
#   XNAT_ADMIN_PASSWORD - XNAT admin password (must match what configure-xnat.sh set)
#   TRUST_NETWORK_NAME  - Docker network the launched container joins; it must reach xnat-web,
#                         because the converter downloads the scan's DICOM and uploads the SEG
#                         through the XNAT REST API.

XNAT_URL="http://xnat-web:8080" # internal to Docker network
NIFTI_TO_SEG_NAME="nifti_to_seg"
# The resource flip.add_mask stores masks in: flip-utils' MASK_RESOURCE (held equal by
# trust/xnat/tests/test_configure_nifti_to_seg.py).
NIFTI_TO_SEG_RESOURCE="NIFTI_SEG"
SUBSCRIPTION_PREFIX="Convert FL job masks to DICOM-SEG"
STATUSES="UPDATED"

if [[ -z "${TRUST_NETWORK_NAME:-}" ]]; then
  echo "ERROR: TRUST_NETWORK_NAME is not set. The nifti_to_seg container would start with no" >&2
  echo "       route to xnat-web and every conversion would fail." >&2
  exit 1
fi

# Helper: curl wrapper for XNAT's REST API. Mirrors configure-dcm2niix.sh — on 2xx it emits
# the response body on stdout; on anything else it reports the request and body on stderr and
# returns non-zero, which `set -e` turns into an abort at the call site.
xnat_curl() {
  local response status body curl_exit=0
  response=$(curl -sS --connect-timeout 10 --max-time 120 -w '\n%{http_code}' "$@" \
    -u "${XNAT_ADMIN_USER}:${XNAT_ADMIN_PASSWORD}") || curl_exit=$?
  if [[ "$curl_exit" -ne 0 ]]; then
    echo "ERROR: curl transport failure (exit $curl_exit)" >&2
    echo "  args: $*" >&2
    return 1
  fi
  status=$(printf '%s' "$response" | tail -n1)
  body=$(printf '%s' "$response" | sed '$d')
  status=${status//$'\r'/}
  body=${body//$'\r'/}
  if ! [[ "$status" =~ ^2[0-9]{2}$ ]]; then
    echo "ERROR: XNAT request failed with HTTP $status" >&2
    echo "  args: $*" >&2
    echo "  body: $body" >&2
    return 1
  fi
  printf '%s' "$body"
}

# ----------------------------------------------------------------
# COMMAND
# ----------------------------------------------------------------

echo "Checking if $NIFTI_TO_SEG_NAME command exists..."
COMMAND_ID=$(xnat_curl "$XNAT_URL/xapi/commands?name=$NIFTI_TO_SEG_NAME" | jq -r '.[0].id // empty')
if [[ -n "$COMMAND_ID" ]]; then
  echo "Found existing command ID: $COMMAND_ID. Deleting..."
  xnat_curl -X DELETE "$XNAT_URL/xapi/commands/$COMMAND_ID" >/dev/null
fi

echo "Adding $NIFTI_TO_SEG_NAME command on network $TRUST_NETWORK_NAME..."
CMD_ID=$(jq --arg network "$TRUST_NETWORK_NAME" '.network = $network' nifti_to_seg_command.json \
  | xnat_curl -X POST "$XNAT_URL/xapi/commands" -H "Content-Type: application/json" -d @-)
if ! [[ "$CMD_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: POST /xapi/commands did not return a numeric command id." >&2
  echo "  body: $CMD_ID" >&2
  exit 1
fi

WRAPPER_NAME=$(jq -r '.xnat[0].name // empty' nifti_to_seg_command.json)
# The event-service action is keyed by the wrapper's id, not the command's (see
# imaging_api/services/projects.py get_wrapper_action_key): read it from the stored command.
WRAPPER_ID=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID" \
  | jq -r --arg name "$WRAPPER_NAME" '.xnat[] | select(.name == $name) | .id')
if ! [[ "$WRAPPER_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: command $CMD_ID has no wrapper '$WRAPPER_NAME' with a numeric id." >&2
  exit 1
fi
echo "Command ID: $CMD_ID, wrapper $WRAPPER_NAME ($WRAPPER_ID)"

echo "Enabling $NIFTI_TO_SEG_NAME command site-wide..."
xnat_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$WRAPPER_NAME/enabled" >/dev/null

# ----------------------------------------------------------------
# EVENT SUBSCRIPTIONS
# ----------------------------------------------------------------

# Replace any previous copies (by prefix, so an older CREATED one goes too), so a re-run does not add
# subscriptions that each launch a container.
echo "Removing any previous '$SUBSCRIPTION_PREFIX' subscriptions..."
EXISTING=$(xnat_curl "$XNAT_URL/xapi/events/subscriptions" \
  | jq -r --arg prefix "$SUBSCRIPTION_PREFIX" '.[] | select(.name | startswith($prefix)) | .id')
for SUB_ID in $EXISTING; do
  echo "  deleting subscription $SUB_ID"
  xnat_curl -X DELETE "$XNAT_URL/xapi/events/subscription/$SUB_ID" >/dev/null
done

for STATUS in $STATUSES; do
  echo "Creating the $STATUS subscription on resource label $NIFTI_TO_SEG_RESOURCE..."
  jq --arg name "$SUBSCRIPTION_PREFIX ($STATUS)" \
     --arg status "$STATUS" \
     --arg action "org.nrg.containers.services.CommandActionProvider:$WRAPPER_ID" \
     --arg filter "@.label == \"$NIFTI_TO_SEG_RESOURCE\"" \
     '.name = $name | .["event-selector"] += $status | .["action-key"] = $action
      | .["event-filter"].status = $status | .["event-filter"]["payload-filter"] = $filter' \
     nifti_to_seg_event.json \
    | xnat_curl -X POST "$XNAT_URL/xapi/events/subscription" -H "Content-Type: application/json" -d @- >/dev/null
done

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

echo " "
echo "Validating nifti_to_seg setup..."
WRAPPER_ENABLED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$WRAPPER_NAME/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: nifti_to_seg wrapper is not enabled site-wide (expected true)" >&2
  exit 1
fi

# XNAT stores a subscription whose event class it cannot resolve and then never fires it, so check
# the class it resolved, and that each subscription is valid and active (see configure-export-mask.sh).
SUBSCRIPTIONS=$(xnat_curl "$XNAT_URL/xapi/events/subscriptions")
for STATUS in $STATUSES; do
  SUB=$(printf '%s' "$SUBSCRIPTIONS" \
    | jq -c --arg name "$SUBSCRIPTION_PREFIX ($STATUS)" '[.[] | select(.name == $name)]')
  if [ "$(printf '%s' "$SUB" | jq length)" != "1" ]; then
    echo "ERROR: expected exactly 1 '$SUBSCRIPTION_PREFIX ($STATUS)' subscription" >&2
    exit 1
  fi
  SUB_EVENT_TYPE=$(printf '%s' "$SUB" | jq -r '.[0]["event-filter"]["event-type"] // empty')
  SUB_VALID=$(printf '%s' "$SUB" | jq -r '.[0].valid')
  SUB_ACTIVE=$(printf '%s' "$SUB" | jq -r '.[0].active')
  echo "  $STATUS: event-type $SUB_EVENT_TYPE (valid: $SUB_VALID, active: $SUB_ACTIVE)"
  case "$SUB_EVENT_TYPE" in
    *.ResourceEvent) ;;
    *)
      echo "ERROR: subscription event-type is '$SUB_EVENT_TYPE', expected a ResourceEvent." >&2
      exit 1
      ;;
  esac
  if [ "$SUB_VALID" != "true" ] || [ "$SUB_ACTIVE" != "true" ]; then
    echo "ERROR: the $STATUS subscription is not valid and active" >&2
    exit 1
  fi
done

echo " "
echo "✅ nifti_to_seg configuration complete and validated!"
