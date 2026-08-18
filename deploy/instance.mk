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

# Single source of truth for parallel dev stacks (FLIP#957). Included from the root
# Makefile and trust/Makefile after their .env* / kit include blocks, so FLIP_INSTANCE
# is already resolved when the prefix is derived.
#
# FLIP_INSTANCE names a hub instance so a second dev stack can run alongside the default
# one. Unset (the norm) every derived name is byte-identical to what it was before this
# knob existed, so existing deployments are untouched.
#
# The compose files derive the same prefix themselves, from ${FLIP_INSTANCE} rather than
# from INSTANCE_PREFIX: they are also invoked directly, without make (`docker compose -f
# deploy/compose.development.yml ...` — see CONTRIBUTING.md and the per-service
# Makefiles), where only the environment exists and a make variable would render empty —
# silently de-prefixing a secondary stack onto the default stack's networks. FLIP_INSTANCE
# is therefore the single user-facing knob and is exported here, which is what makes the
# make-side and compose-side spellings agree on every invocation path, `make -C trust`
# included.
export FLIP_INSTANCE
INSTANCE_PREFIX := $(if $(FLIP_INSTANCE),$(FLIP_INSTANCE)-,)

# COMPOSE_PROJECT matters as much as the names themselves: compose derives the project
# from the directory of the first -f file, which is always `deploy/`, so without this BOTH
# stacks would land in one project (`deploy`) regardless of which checkout they were
# launched from, and `up` on one would reconcile — and tear down — the other's containers.
# `-p deploy` is exactly the implicit value used today, so pinning it changes nothing for
# the default stack. Deliberately `:=`, not `?=`: a stray COMPOSE_PROJECT inherited from
# the environment silently repointing `-p` is the very failure this exists to prevent.
COMPOSE_PROJECT := $(INSTANCE_PREFIX)deploy

# Idempotent create for one hub-shared bridge network, whose name carries the prefix above.
# Inspect first so an existing network is a quiet no-op; inspect again after a failed create
# so losing the race with a concurrent `create-networks` still counts as success. Called as
# $(call ensure_bridge_network,<name>) from the create-networks targets in both Makefiles.
define ensure_bridge_network
	@{ docker network inspect $(1) >/dev/null 2>&1 || docker network create --driver bridge $(1) >/dev/null || docker network inspect $(1) >/dev/null 2>&1 || { echo "❌ Could not create Docker network $(1) — see the daemon error above."; exit 1; }; }
endef
