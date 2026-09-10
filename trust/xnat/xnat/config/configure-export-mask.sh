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

# Registers the export_mask container-service command and its event subscription.
#
# When a user saves a segmentation in the XNAT OHIF viewer it lands as a DICOM-SEG assessor.
# This command converts that assessor to NIfTI and uploads it back to the session, which is
# the format FL training consumes. Nothing upstream does this — see the XNAT discussion list
# thread on MONAI Label + OHIF, where the missing DICOM-SEG -> NIfTI step is the open problem.
#
# Run ONLY on trusts that enable MONAI Label — `make xnat-configure` gates this on
# MONAI_LABEL=true (see xnat-configure-export-mask in trust/xnat/Makefile). A trust without
# MONAI Label has no OHIF viewer plugin, so it can never create the assessors this reacts to;
# registering the converter command and a site-wide subscription there would be pure blast
# radius. Turning MONAI Label on later requires re-running `make xnat-configure`.
#
# Must run AFTER configure-dcm2niix.sh, which registers the Docker backend and verifies the
# socket proxy. The subscription created here is deliberately site-wide: a DICOM-SEG can be
# created in any project by anyone using the OHIF viewer, and there is no per-project opt-in
# for it (unlike dcm2niix's dicom_to_nifti flag).
#
# Required environment variables:
#   XNAT_ADMIN_USER     - XNAT admin username
#   XNAT_ADMIN_PASSWORD - XNAT admin password (must match what configure-xnat.sh set)
#   TRUST_NETWORK_NAME  - Docker network the launched container joins; it must reach xnat-web,
#                         because the converter pulls the session's NIfTI resources and uploads
#                         its output through the XNAT REST API (unlike dcm2niix, which only
#                         touches mounted files and therefore needs no network).

XNAT_URL="http://xnat-web:8080" # internal to Docker network
EXPORT_MASK_NAME="export_mask"
EXPORT_MASK_SUBSCRIPTION_NAME="Convert exported OHIF masks to NIfTI"

if [[ -z "${TRUST_NETWORK_NAME:-}" ]]; then
  echo "ERROR: TRUST_NETWORK_NAME is not set. The export_mask container would start with no" >&2
  echo "       route to xnat-web and every conversion would fail at upload time." >&2
  exit 1
fi

# Helper: curl wrapper for XNAT's REST API. Mirrors configure-dcm2niix.sh — on 2xx it emits
# the response body on stdout; on anything else it reports the request and body on stderr and
# returns non-zero, which `set -e` turns into an abort at the call site (so don't call it
# inside `if`/`||` without handling the failure).
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
  # Strip CRs so a middlebox emitting \r\n can't break the numeric guards below.
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

echo "Checking if $EXPORT_MASK_NAME command exists..."
COMMAND_ID=$(xnat_curl "$XNAT_URL/xapi/commands?name=$EXPORT_MASK_NAME" | jq -r '.[0].id // empty')
if [[ -n "$COMMAND_ID" ]]; then
  echo "Found existing command ID: $COMMAND_ID. Deleting..."
  xnat_curl -X DELETE "$XNAT_URL/xapi/commands/$COMMAND_ID" >/dev/null
fi

# Pin the launched container to this trust's network. The JSON ships a placeholder rather
# than a name because the network is per-trust (deploy_trust-network-<N>).
echo "Adding $EXPORT_MASK_NAME command on network $TRUST_NETWORK_NAME..."
CMD_ID=$(jq --arg network "$TRUST_NETWORK_NAME" '.network = $network' export_mask_command.json \
  | xnat_curl -X POST "$XNAT_URL/xapi/commands" -H "Content-Type: application/json" -d @-)

# POST /xapi/commands returns the new command's numeric id as a bare JSON number (see the
# longer note in configure-dcm2niix.sh), so guard on that rather than re-GETting by name.
if ! [[ "$CMD_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: POST /xapi/commands did not return a numeric command id." >&2
  echo "  body: $CMD_ID" >&2
  exit 1
fi

WRAPPER_NAME=$(jq -r '.xnat[0].name // empty' export_mask_command.json)
if [[ -z "$WRAPPER_NAME" ]]; then
  echo "ERROR: no .xnat[0].name wrapper in export_mask_command.json." >&2
  exit 1
fi

echo "Command ID: $CMD_ID"
echo "Wrapper Name: $WRAPPER_NAME"

echo "Enabling $EXPORT_MASK_NAME command site-wide..."
xnat_curl -X PUT "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$WRAPPER_NAME/enabled" >/dev/null

# ----------------------------------------------------------------
# EVENT SUBSCRIPTION
# ----------------------------------------------------------------

# Replace any previous copy so re-running this script is idempotent rather than accumulating
# duplicate subscriptions that would each launch a container for the same assessor.
echo "Removing any previous '$EXPORT_MASK_SUBSCRIPTION_NAME' subscription..."
EXISTING=$(xnat_curl "$XNAT_URL/xapi/events/subscriptions" \
  | jq -r --arg name "$EXPORT_MASK_SUBSCRIPTION_NAME" '.[] | select(.name == $name) | .id')
for SUB_ID in $EXISTING; do
  echo "  deleting subscription $SUB_ID"
  xnat_curl -X DELETE "$XNAT_URL/xapi/events/subscription/$SUB_ID" >/dev/null
done

echo "Creating event subscription for $EXPORT_MASK_NAME..."
sed "s/\${CMD_ID}/$CMD_ID/g" export_mask_event.json \
  | xnat_curl -X POST "$XNAT_URL/xapi/events/subscription" \
      -H "Content-Type: application/json" -d @- >/dev/null

# ----------------------------------------------------------------
# VALIDATION
# ----------------------------------------------------------------

# Every xnat_curl above aborts on a non-2xx, so reaching here means each write was accepted;
# these re-GETs check the resulting state actually persisted.
echo " "
echo "Validating export_mask setup..."
WRAPPER_ENABLED=$(xnat_curl "$XNAT_URL/xapi/commands/$CMD_ID/wrappers/$WRAPPER_NAME/enabled")
if [ "$WRAPPER_ENABLED" != "true" ]; then
  echo "ERROR: export_mask wrapper is not enabled site-wide (expected true)" >&2
  echo "  body: $WRAPPER_ENABLED" >&2
  exit 1
fi

SUBSCRIPTIONS=$(xnat_curl "$XNAT_URL/xapi/events/subscriptions")

SUB_COUNT=$(printf '%s' "$SUBSCRIPTIONS" \
  | jq -r --arg name "$EXPORT_MASK_SUBSCRIPTION_NAME" '[.[] | select(.name == $name)] | length')
if [ "$SUB_COUNT" != "1" ]; then
  echo "ERROR: expected exactly 1 '$EXPORT_MASK_SUBSCRIPTION_NAME' subscription, found $SUB_COUNT" >&2
  exit 1
fi

# Counting by name alone is not enough. XNAT accepts a subscription whose event-selector names
# a class it cannot resolve, and stores it happily — the subscription then simply never fires,
# so DICOM-SEG assessors are created and silently never converted. Nothing surfaces in the
# viewer, and the only trace is the absence of entries in Command History. Assert the selector
# actually round-tripped as an ImageAssessorEvent, and that the subscription is active.
SUB_SELECTOR=$(printf '%s' "$SUBSCRIPTIONS" \
  | jq -r --arg name "$EXPORT_MASK_SUBSCRIPTION_NAME" '.[] | select(.name == $name) | .["event-selector"] // empty')
SUB_ACTIVE=$(printf '%s' "$SUBSCRIPTIONS" \
  | jq -r --arg name "$EXPORT_MASK_SUBSCRIPTION_NAME" '.[] | select(.name == $name) | .active')

echo "  event-selector: $SUB_SELECTOR"
# Substring rather than equality: XNAT is free to normalise the stored form, and a spurious
# bring-up failure over cosmetic drift would be worse than the bug this guards against.
case "$SUB_SELECTOR" in
  *ImageAssessorEvent*) ;;
  *)
    echo "ERROR: subscription event-selector is '$SUB_SELECTOR', expected an ImageAssessorEvent." >&2
    echo "       XNAT event classes carry the 'Event' suffix; a selector it cannot resolve is" >&2
    echo "       stored without complaint and then never fires." >&2
    exit 1
    ;;
esac

if [ "$SUB_ACTIVE" != "true" ]; then
  echo "ERROR: '$EXPORT_MASK_SUBSCRIPTION_NAME' subscription is not active (got '$SUB_ACTIVE')" >&2
  exit 1
fi

echo " "
echo "✅ export_mask configuration complete and validated!"
