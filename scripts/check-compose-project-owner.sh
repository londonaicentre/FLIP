#!/usr/bin/env bash
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

# Refuse to act on a hub compose project that another checkout owns (FLIP#1227).
#
# Compose project names are global to the docker daemon and derive from the directory of the
# first -f file — always deploy/ — so on a shared host two checkouts without FLIP_INSTANCE both
# land on project `deploy`, and `up` in one recreates the other's containers with the wrong env
# (observed 2026-09-16: a colleague's hub was rebuilt over its own DB volume). The trust side
# has check_slot_not_taken (trust/Makefile); this is the hub equivalent. The hub project carries
# no --env-file, so the only owner signal is compose's working_dir label.
#
# A project passes when it has no containers, or every container's working_dir is either this
# checkout or another directory owned by the current uid (a same-user worktree may hand the
# stack over). Anything else — another user's path, or one that no longer exists — is refused.
#
# Inputs (passed as environment variables by the Makefile):
#   COMPOSE_PROJECT        the project `up`/`down` is about to drive (deploy/instance.mk)
#   EXPECTED_WORKING_DIR   this checkout's deploy/ directory
#   FORCE                  1 to override (the same knob generate-*-key and register-trust use)

set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-deploy}"
EXPECTED_WORKING_DIR="${EXPECTED_WORKING_DIR:-}"
FORCE="${FORCE:-}"

[ "${FORCE}" = "1" ] && exit 0

# No docker, or a daemon we cannot reach: compose will fail with the better message.
command -v docker >/dev/null 2>&1 || exit 0
owners=$(docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null | grep -v '^$' | sort -u) || exit 0
[ -n "${owners}" ] || exit 0

expected=$(realpath -m "${EXPECTED_WORKING_DIR}" 2>/dev/null || printf '%s' "${EXPECTED_WORKING_DIR}")
me=$(id -u)
foreign=""
while IFS= read -r dir; do
    [ -n "${dir}" ] || continue
    resolved=$(realpath -m "${dir}" 2>/dev/null || printf '%s' "${dir}")
    [ "${resolved}" = "${expected}" ] && continue
    owner=$(stat -c '%u' "${resolved}" 2>/dev/null || echo "?")
    [ "${owner}" = "${me}" ] && continue
    if [ "${owner}" = "?" ]; then
        foreign="${foreign}
       ${dir}  (not readable by you, or no longer exists)"
    else
        foreign="${foreign}
       ${dir}  (owned by $(stat -c '%U' "${resolved}" 2>/dev/null || echo "uid ${owner}"))"
    fi
done <<EOF
${owners}
EOF

[ -n "${foreign}" ] || exit 0

echo "❌ Compose project '${COMPOSE_PROJECT}' already has containers from another checkout:${foreign}" >&2
echo "   This checkout is ${expected}. Acting on the project would recreate or tear down" >&2
echo "   someone else's hub with this env file." >&2
echo "   On a shared host give your stack its own instance — FLIP_INSTANCE=<name> in" >&2
echo "   $(basename "${MAIN_ENV_FILE:-.env.development}") — and its own host ports (CLAUDE.md, FLIP_INSTANCE)." >&2
echo "   List the containers with:" >&2
echo "       docker ps -a --filter label=com.docker.compose.project=${COMPOSE_PROJECT}" >&2
echo "   If they are yours and stale, remove them from the checkout that created them, or" >&2
echo "   re-run with FORCE=1 to override." >&2
exit 1
