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

# Every write the NVFLARE fl-client needs in its kit, done once before the client starts (FLIP#1570), so
# the client mounts the kit's local/ and startup/ read-only: the kit and the rendered site config are
# read-only to the running client. Run as the client's user by the fl-client-init Compose service and
# the chart's fl-client-init init container:
#
#   /app/fl-client-init.sh <kit dir>     # the dir holding the kit's local/ and startup/
#
# Reads NUM_AVAILABLE_GPUS, MEMORY_PER_GPU_IN_GIB, LOG_LEVEL, FL_SITE_PRIVACY_* and ACCESS_POLICY_FILE, as
# the client would. Any failure, an invalid site privacy configuration included, fails the step, and the
# client, which waits for it, does not start (fail closed).
set -euo pipefail

kit="${1:?usage: fl-client-init.sh <kit dir holding local/ and startup/>}"
kit="${kit%/}"

# Under a kit's FLIP_UID (FLIP#1731) this uid has no passwd entry, as in the client's entrypoint.sh.
if ! id -un >/dev/null 2>&1 || [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp USER=flip LOGNAME=flip
fi

if [ ! -w "$kit/local" ]; then
    echo "[fl-client-init] cannot write $kit/local: it must be owned by uid $(id -u), the client's user" >&2
    exit 1
fi
# Drop the previous render first, so a failure below leaves this kit without one and its client
# refuses to start rather than run on a stale resources.json.
rm -f "$kit/local/resources.json" "$kit/local/log_config.json"

# Whether the GPU values were given, before the defaults below: the .default fallback cannot apply them.
gpu_env="${NUM_AVAILABLE_GPUS:-}${MEMORY_PER_GPU_IN_GIB:-}"
export NUM_AVAILABLE_GPUS="${NUM_AVAILABLE_GPUS:-1}"
export MEMORY_PER_GPU_IN_GIB="${MEMORY_PER_GPU_IN_GIB:-16}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Provisioning ships these executable; set the bit for a kit copied without it.
chmod u+x "$kit/startup/start.sh" "$kit/startup/sub_start.sh" "$kit/startup/stop_fl.sh" 2>/dev/null || true

# Fill the kit's <name>.template.json, or fall back to <name>.json.default for a kit from before the
# templates. Written beside the target and renamed.
# resources.template.json tells NVFLARE's scheduler the GPUs this client has: provisioning leaves the count
# and memory as ${NUM_AVAILABLE_GPUS} and ${MEMORY_PER_GPU_IN_GIB} (provision/scripts/restructure-lib.sh),
# while resources.json.default holds what provisioning wrote, not this deployment's count (FLIP#1509).
render() {
    local name="$1" vars="$2" out="$kit/local/$1.json"
    if [ -f "$kit/local/$name.template.json" ]; then
        envsubst "$vars" < "$kit/local/$name.template.json" > "$out.tmp"
        echo "[fl-client-init] $kit: $name.json rendered from $name.template.json ($3)"
    else
        cp "$kit/local/$name.json.default" "$out.tmp"
        echo "[fl-client-init] $kit: $name.json copied from $name.json.default (the kit has no $name.template.json)"
    fi
    mv -f "$out.tmp" "$out"
}
# shellcheck disable=SC2016 # envsubst's list of the only variables to substitute
render resources '${NUM_AVAILABLE_GPUS} ${MEMORY_PER_GPU_IN_GIB}' \
    "NUM_AVAILABLE_GPUS=$NUM_AVAILABLE_GPUS, MEMORY_PER_GPU_IN_GIB=$MEMORY_PER_GPU_IN_GIB"
if [ -n "$gpu_env" ] && [ ! -f "$kit/local/resources.template.json" ]; then
    echo "[fl-client-init] WARNING: NUM_AVAILABLE_GPUS / MEMORY_PER_GPU_IN_GIB are NOT applied: the client" \
        "advertises the GPUs provisioning wrote. Re-package the kit to get resources.template.json." >&2
fi
# shellcheck disable=SC2016
render log_config '${LOG_LEVEL}' "LOG_LEVEL=$LOG_LEVEL"

# Site privacy policy (FLIP#851, FLIP#1259): render local/privacy.json from FL_SITE_PRIVACY_* or the
# governance document's [fl_privacy.nvflare] section, or remove a stale render when neither is set.
python -m flip.nvflare.site_policy "$kit/local/privacy.json"
