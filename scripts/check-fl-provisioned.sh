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

# Fail fast when a backend's per-net credentials are missing, instead of letting the FL
# containers crash-loop (NVFLARE's fl-server logs "start.sh: No such file or directory";
# Flower's SuperLink dies on an absent TLS cert). Neither backend's credentials are created
# by `make up` — they come from `make -C fl-services/<backend> provision...`. Net IDs come
# from NET_ENDPOINTS (same source as _ensure-fl-jobs-dir).
#
# Inputs (passed as environment variables by the Makefile):
#   FL_BACKEND          nvflare | flower
#   NET_ENDPOINTS       JSON object whose keys are the net IDs (e.g. {"net-1": "..."})
#   FL_PROVISIONED_DIR  per-backend credential root, derived in deploy/fl_backend.mk:
#                         nvflare → <net>/services/fl-server-<net>/startup/start.sh
#                         flower  → <net>/certificates/ca.crt
#   FL_API_KEY_GID      development only (unset elsewhere): the gid the NVFLARE FL API joins
#                       (group_add), which must own and be able to read its admin client.key

set -euo pipefail

FL_BACKEND="${FL_BACKEND:-nvflare}"
NET_ENDPOINTS="${NET_ENDPOINTS:-}"
FL_PROVISIONED_DIR="${FL_PROVISIONED_DIR:-fl-services/nvflare/provision/workspace-dev}"

case "${FL_BACKEND}" in
    nvflare)
        marker_suffix="services/fl-server-NET/startup/start.sh"
        missing_desc="NVFLARE workspace not provisioned"
        missing_hint="Startup kits are missing under ${FL_PROVISIONED_DIR}/<net>/services/"
        provision_cmds="       make -C fl-services/nvflare provision-2-nets          # net-1 + net-2
       make -C fl-services/nvflare provision NET_NUMBER=<N>  # a single net"
        backend_readme="fl-services/nvflare/README.md"
        ;;
    flower)
        marker_suffix="certificates/ca.crt"
        missing_desc="Flower credentials not provisioned"
        missing_hint="TLS certs are missing under ${FL_PROVISIONED_DIR}/<net>/certificates/"
        provision_cmds="       make -C fl-services/flower provision NET_NUMBER=<N>   # repeat per net"
        backend_readme="fl-services/flower/README.md"
        ;;
    *)
        # An unknown backend has no known credential layout; deploy/fl_backend.mk rejects it
        # anyway, so stay silent rather than guess.
        exit 0
        ;;
esac

# jq derives the net IDs from NET_ENDPOINTS' JSON keys; require it explicitly (it's already
# used across the repo) so a minimal environment fails with a clear message rather than a
# cryptic parse error.
if ! command -v jq >/dev/null 2>&1; then
    echo "❌ check-fl-provisioned: jq not found on PATH; required to parse NET_ENDPOINTS" >&2
    exit 1
fi

# Empty *or* unparseable NET_ENDPOINTS both collapse to an empty `nets` (jq's stderr is
# suppressed and `|| nets=""` keeps `set -e` from aborting first), so the guard below
# reports either case with one clear message.
nets=$(printf '%s' "${NET_ENDPOINTS}" | jq -r 'keys_unsorted | join(" ")' 2>/dev/null) || nets=""
if [ -z "${nets}" ]; then
    echo "❌ check-fl-provisioned: NET_ENDPOINTS is empty or unparseable; cannot derive net IDs" >&2
    exit 1
fi

missing=""
for net in ${nets}; do
    marker="${FL_PROVISIONED_DIR}/${net}/$(printf '%s' "${marker_suffix}" | sed "s/NET/${net}/g")"
    [ -f "${marker}" ] || missing="${missing} ${net}"
done

if [ -n "${missing}" ]; then
    echo "❌ ${missing_desc} for net(s):${missing}" >&2
    echo "   ${missing_hint} and are NOT created by 'make up'." >&2
    echo "   Provision first:" >&2
    printf '%s\n' "${provision_cmds}" >&2
    echo "   See ${backend_readme}." >&2
    exit 1
fi

# NVFLARE writes private keys 0600, and in development the FL API reads its admin key through the
# host group (group_add in deploy/compose.development.nvflare.yml). `make provision` opens the key to
# that group; a workspace provisioned before it did, or by another user, leaves a key the FL API
# cannot open, and it would die at boot with a bare PermissionError (FLIP#1384). The marker above is
# the FL server's kit, so a workspace with no FL API kit (an interrupted or partial provision) passes
# it; the compose file mounts that kit for every net, so a missing key is the same boot failure.
if [ "${FL_BACKEND}" = nvflare ] && [ -n "${FL_API_KEY_GID:-}" ]; then
    absent=""
    unreadable=""
    for net in ${nets}; do
        key="${FL_PROVISIONED_DIR}/${net}/services/flip-fl-api-${net}/startup/client.key"
        if [ ! -f "${key}" ]; then
            absent="${absent} ${key}"
            continue
        fi
        read -r gid mode < <(stat -c '%g %a' "${key}")
        if [ "${gid}" != "${FL_API_KEY_GID}" ] || [ $(( 8#${mode} & 040 )) -eq 0 ]; then
            unreadable="${unreadable} ${key}"
        fi
    done
    if [ -n "${absent}" ]; then
        echo "❌ The NVFLARE FL API admin key is missing (the workspace has no complete FL API kit):" >&2
        for key in ${absent}; do echo "     ${key}" >&2; done
        echo "   Re-provision:  make -C fl-services/nvflare provision NET_NUMBER=<N>" >&2
        exit 1
    fi
    if [ -n "${unreadable}" ]; then
        echo "❌ The NVFLARE FL API cannot read its admin key (it needs group ${FL_API_KEY_GID}, mode 640):" >&2
        for key in ${unreadable}; do echo "     ${key}" >&2; done
        echo "   Fix it in place:  chgrp ${FL_API_KEY_GID} <key> && chmod 640 <key>" >&2
        echo "   or re-provision:  make -C fl-services/nvflare provision NET_NUMBER=<N>" >&2
        exit 1
    fi
fi
