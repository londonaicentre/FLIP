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


# Under a kit's FLIP_UID (FLIP#1731) this uid has no passwd entry, so HOME is "/" and
# getpass.getuser() raises during torch's import; a uid the image does know (nobody, 65534) has a
# home it cannot write. Either way, name the user and give it a home it can write.
if ! id -un >/dev/null 2>&1 || [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp USER=flip LOGNAME=flip
fi

#################################################################
###### Kit check ################################################
#################################################################
# The kit's local/ and startup/ are mounted read-only (FLIP#1570): fl-client-init
# (/app/fl-client-init.sh) rendered resources.json, log_config.json and the site
# privacy policy before this container started. Start without writing to the kit,
# and refuse to start on a render that is missing or does not match this container's
# configuration (fail closed): an unfiltered client must never run.
export NUM_AVAILABLE_GPUS=${NUM_AVAILABLE_GPUS:-1}
export MEMORY_PER_GPU_IN_GIB=${MEMORY_PER_GPU_IN_GIB:-16}
export LOG_LEVEL=${LOG_LEVEL:-INFO}
for cfg in /app/local/resources.json /app/local/log_config.json; do
    if [ ! -f "$cfg" ]; then
        echo "❌ [entrypoint] ${cfg} is missing — fl-client-init must run before the fl-client; refusing to start" >&2
        exit 1
    fi
done
if ! python -m flip.nvflare.site_policy --assert-rendered /app/local/privacy.json; then
    echo "❌ [entrypoint] the site privacy policy in /app/local is missing, stale or invalid (FL_SITE_PRIVACY_* or the governance document) — refusing to start fl-client (fail closed)" >&2
    exit 1
fi
#################################################################

# Cleanup any existing processes
echo "[entrypoint] Cleaning up stale processes and files..."
pkill -f "nvflare" || true
rm -f /app/pid.fl /app/daemon_pid.fl

# Start FL client
echo "[entrypoint] Starting FL client with:"
echo "  NUM_AVAILABLE_GPUS=${NUM_AVAILABLE_GPUS}"
echo "  MEMORY_PER_GPU_IN_GIB=${MEMORY_PER_GPU_IN_GIB}"
echo "  LOG_LEVEL=${LOG_LEVEL}"
cd /app/startup

# Call the startup script with the provided parameters
./start.sh

# Handle graceful shutdown
trap "echo '[entrypoint] Caught SIGTERM, stopping ...'; /app/startup/stop_fl.sh && rm -f /app/pid.fl /app/daemon_pid.fl" SIGTERM

# Wait for background processes
wait
