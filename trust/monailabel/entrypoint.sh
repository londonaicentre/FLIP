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

# Starts the MONAI Label server against this trust's XNAT and registers it with the XNAT
# OHIF viewer so the Masks panel offers AI-assisted annotation.
#
# Required environment (supplied by trust/deploy/compose_trust.*.monailabel.yml):
#   MONAI_LABEL_PORT               - port the server listens on
#   MONAI_LABEL_DATASTORE_URL      - XNAT base URL, reachable inside the trust network
#   MONAI_LABEL_DATASTORE_USERNAME - XNAT service account
#   MONAI_LABEL_DATASTORE_PASSWORD - XNAT service account password
#   MONAI_LABEL_PUBLIC_URL         - URL the CLINICIAN'S BROWSER uses to reach this server
#   MONAI_LABEL_MODELS             - comma-separated radiology models to load

set -euo pipefail

APP_DIR=/workspace/app/radiology
SERVER_URL="http://127.0.0.1:${MONAI_LABEL_PORT}"

# --- Wait for XNAT ------------------------------------------------------------------
# Wall-clock bounded, and each probe carries its own timeout, so a wedged XNAT fails
# loudly instead of printing dots forever (mirrors trust/xnat/xnat/config/configure-xnat.sh).
echo "Waiting for XNAT at ${MONAI_LABEL_DATASTORE_URL} ..."
deadline=$((SECONDS + 900))
until curl --output /dev/null --silent --head --fail \
  --connect-timeout 5 --max-time 10 "${MONAI_LABEL_DATASTORE_URL}/app/template/Login.vm"; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: XNAT did not become available within 900s" >&2
    exit 1
  fi
  printf '.'
  sleep 5
done
echo "XNAT is up!"

# XNAT answers on the login page before configure-xnat.sh has finished creating the service
# account, so poll until THESE credentials actually authenticate rather than sleeping a
# fixed interval and hoping. Without this the server starts, fails to log in, and exits.
echo "Waiting for the XNAT service account to be usable..."
deadline=$((SECONDS + 900))
until curl --output /dev/null --silent --fail \
  --connect-timeout 5 --max-time 10 \
  -u "${MONAI_LABEL_DATASTORE_USERNAME}:${MONAI_LABEL_DATASTORE_PASSWORD}" \
  "${MONAI_LABEL_DATASTORE_URL}/data/projects?format=json"; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: XNAT service account '${MONAI_LABEL_DATASTORE_USERNAME}' could not authenticate within 900s" >&2
    exit 1
  fi
  printf '.'
  sleep 5
done
echo "XNAT service account OK."

# --- Start the MONAI Label server ---------------------------------------------------
# --studies is what the XNAT datastore uses as its API URL (MONAI_LABEL_DATASTORE_URL is
# not read for the xnat datastore, only for our own probes above).
echo "Starting MONAI Label server (models: ${MONAI_LABEL_MODELS}) ..."
monailabel start_server \
  --app "${APP_DIR}" \
  --studies "${MONAI_LABEL_DATASTORE_URL}" \
  --host 0.0.0.0 --port "${MONAI_LABEL_PORT}" \
  --conf models "${MONAI_LABEL_MODELS}" &
server_pid=$!

# --- Wait for it to be serving ------------------------------------------------------
# Poll the app's own info endpoint instead of sleeping: loading models downloads pretrained
# weights, which takes minutes on a cold volume and seconds on a warm one.
echo "Waiting for MONAI Label to finish loading models..."
deadline=$((SECONDS + 1800))
until curl --output /dev/null --silent --fail \
  --connect-timeout 5 --max-time 10 "${SERVER_URL}/info/"; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "ERROR: MONAI Label server exited during startup" >&2
    wait "$server_pid"
    exit 1
  fi
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    echo "ERROR: MONAI Label server did not become ready within 1800s" >&2
    exit 1
  fi
  printf '.'
  sleep 5
done
echo "MONAI Label server is ready."

# --- Register with the XNAT OHIF viewer ---------------------------------------------
# The OHIF viewer calls this URL from the clinician's BROWSER — XNAT's ohifaiaa xapi only
# stores and validates the string, it never proxies the request. So it must be an address
# that resolves on the clinician's machine; a Docker-internal name or 0.0.0.0 will not work.
# PUT /xapi/ohifaiaa/servers is restricted to XNAT admins, which the trust service account is.
echo "Registering MONAI Label with the XNAT OHIF viewer: ${MONAI_LABEL_PUBLIC_URL}"
register_status=$(curl -s -o /tmp/ohifaiaa-put.json --max-time 60 -w "%{http_code}" \
  -u "${MONAI_LABEL_DATASTORE_USERNAME}:${MONAI_LABEL_DATASTORE_PASSWORD}" \
  -X PUT "${MONAI_LABEL_DATASTORE_URL}/xapi/ohifaiaa/servers" \
  -H "accept: */*" -H "Content-Type: application/json" \
  -d "[\"${MONAI_LABEL_PUBLIC_URL}\"]") || register_status="000"

if [[ "$register_status" != 2* ]]; then
  # Fail rather than run on: a server that is up but unregistered looks healthy while the
  # MONAI Label panel is simply absent from the viewer, which is hard to diagnose from here.
  echo "ERROR: registering the MONAI Label server with XNAT failed (HTTP $register_status)" >&2
  cat /tmp/ohifaiaa-put.json 2>/dev/null || true
  kill "$server_pid" 2>/dev/null || true
  exit 1
fi
echo "Registered. NOTE: each user must also enable it in the viewer under"
echo "  Options -> Preferences -> Experimental -> MONAI Label"

# Hand the container's lifetime back to the server process.
wait "$server_pid"
