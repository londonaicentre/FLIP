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
# stack over). Anything else — another user's path, one that no longer exists, or a container
# with no working_dir label at all (older compose; owner unidentifiable) — is refused.
#
# Inputs (passed as environment variables by the Makefile):
#   COMPOSE_PROJECT        the project `up`/`down` is about to drive (deploy/instance.mk)
#   EXPECTED_WORKING_DIR   this checkout's deploy/ directory
#   ALLOW_FOREIGN_PROJECT  literally `1` to skip the check. Deliberately its own knob, not the
#                          repo's generic FORCE: `up` also passes FORCE to
#                          generate-internal-service-key, where it rotates the hub's internal
#                          service key and recreates flip-api and every fl-server — so a
#                          shared override would turn "let me past the guard" into "kill the
#                          running FL job". Only `1` counts (stricter than the `$(if $(FORCE))`
#                          truthiness used elsewhere, on purpose: a bypass should not be
#                          settable by accident). Skipping the check does NOT make taking over
#                          or recreating a live stack safe — it only stops this script saying no.

set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-deploy}"
EXPECTED_WORKING_DIR="${EXPECTED_WORKING_DIR:-}"
ALLOW_FOREIGN_PROJECT="${ALLOW_FOREIGN_PROJECT:-}"

[ "${ALLOW_FOREIGN_PROJECT}" = "1" ] && exit 0

# Canonical form of a directory, or the input unchanged when it cannot be entered (missing,
# or under a peer's 0750 home). `cd -P`/`pwd -P` rather than `realpath -m`: not on macOS.
canon() {
    (cd -P -- "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"
}
# Numeric owner of a path, GNU `stat -c` first then BSD `stat -f`; "?" when neither works.
owner_uid() {
    stat -c '%u' -- "$1" 2>/dev/null || stat -f '%u' -- "$1" 2>/dev/null || echo "?"
}
owner_name() {
    stat -c '%U' -- "$1" 2>/dev/null || stat -f '%Su' -- "$1" 2>/dev/null || echo "uid $2"
}

# No docker, or a daemon we cannot reach: compose will fail with the better message.
command -v docker >/dev/null 2>&1 || exit 0
# One line per container: "<id>\t<working_dir label>". Listing ids too means a container whose
# label is empty is still counted — dropping it would let an unidentifiable owner through.
containers=$(docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --format '{{.ID}}\t{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null) || exit 0
[ -n "${containers}" ] || exit 0

expected=$(canon "${EXPECTED_WORKING_DIR}")
me=$(id -u)
foreign=""
while IFS=$'\t' read -r id dir; do
    [ -n "${id}" ] || continue
    if [ -z "${dir}" ]; then
        foreign="${foreign}
       container ${id}  (no working_dir label — owner cannot be identified)"
        continue
    fi
    resolved=$(canon "${dir}")
    [ "${resolved}" = "${expected}" ] && continue
    owner=$(owner_uid "${resolved}")
    [ "${owner}" = "${me}" ] && continue
    if [ "${owner}" = "?" ]; then
        foreign="${foreign}
       ${dir}  (not readable by you, or no longer exists)"
    else
        foreign="${foreign}
       ${dir}  (owned by $(owner_name "${resolved}" "${owner}"))"
    fi
done <<EOF
$(printf '%s\n' "${containers}" | sort -u)
EOF

[ -n "${foreign}" ] || exit 0
# One entry per directory, however many containers came from it.
foreign=$(printf '%s\n' "${foreign}" | grep -v '^$' | sort -u)

echo "❌ Compose project '${COMPOSE_PROJECT}' already has containers from another checkout:" >&2
printf '%s\n' "${foreign}" >&2
echo "   This checkout is ${expected}. Acting on the project would recreate or tear down" >&2
echo "   someone else's hub with this env file." >&2
echo "   On a shared host give your stack its own instance — FLIP_INSTANCE=<name> in" >&2
echo "   $(basename "${MAIN_ENV_FILE:-.env.development}") — and its own host ports (CLAUDE.md, FLIP_INSTANCE)." >&2
echo "   List the containers with:" >&2
echo "       docker ps -a --filter label=com.docker.compose.project=${COMPOSE_PROJECT}" >&2
echo "   If they are yours and stale, remove them from the checkout that created them. To skip" >&2
echo "   this check anyway, re-run with ALLOW_FOREIGN_PROJECT=1 — that only silences the guard;" >&2
echo "   recreating a stack someone else is using is still exactly as disruptive." >&2
exit 1
