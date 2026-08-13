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

.PHONY: build dev prod clean stop up down up-trust down-trust restart-trust up-trust-ec2 down-trust-ec2 \
	up-fl-clients up-fl-clients-kit down-fl-clients down-fl-clients-kit \
	create-networks remove-networks recreate-networks tests \
	update-omop-data update-orthanc-data integration_test
# PROD selects the compose-file suffix (__DCKR_SUFFIX: stag collapses to the
# production compose) and the kit-file env token (ENV: stag stays distinct, so
# a stag kit is .env.<CODE>.stag, never confused with .env.<CODE>.production).
ifeq ($(PROD),true)
__DCKR_SUFFIX=production
ENV=production
else ifeq ($(PROD),stag)
__DCKR_SUFFIX=production
ENV=stag
else
__DCKR_SUFFIX=development
ENV=development
endif

# The trust stack runs against the per-trust kit file (trust/.env.<KIT>) as the
# single source of truth — no hub `.env.*` include. A remote trust operator
# only ever has their kit file, and we want a hub-admin dev box to fail loud
# on a stale kit rather than silently fall back to hub env values. The kit
# file's "Hub-shared (managed)" section, populated by `make register-trusts`
# / `make sync-trust-kits`, carries the values that used to flow from hub env
# (AES_KEY_BASE64, CENTRAL_HUB_API_URL, DOCKER_*, FL_BACKEND, NLB_SUBDOMAIN,
# etc.) — see HUB_SHARED_ENV_KEYS in flip_api/scripts/register_trust.py.
# Kit includes must come BEFORE fl_backend.mk so its $(FL_BACKEND)-derived
# vars resolve against the kit's value, not fl_backend.mk's fallback default.
# Kit file is named by the trust CODE + env: trust/.env.<CODE>.<env>. The
# legacy fallback to .env.<KIT> keeps the not-yet-migrated EC2/on-prem path
# (KIT=<slot>, no env suffix) working during the two-phase rollout; remove it
# once the prod-deploy phase migrates those kits to the CODE+env naming.
KIT_FILE := $(if $(wildcard .env.$(KIT).$(ENV)),.env.$(KIT).$(ENV),.env.$(KIT))
-include $(KIT_FILE)

# Dev only: the kit's Hub-shared block is commented out (inert) — those values
# are inherited from the hub's .env.development (the single source) to avoid a
# drifting copy. Include + export them here so they resolve both via `make up`
# (the root Makefile already exports .env.development) AND a direct
# `make -C trust up-trust …`. Must precede fl_backend.mk so FL_BACKEND is set.
# Prod kits carry Hub-shared live, so prod stays kit-only (no hub .env include —
# a remote operator never has the hub env).
ifeq ($(ENV),development)
-include ../.env.development
export AES_KEY_BASE64 CENTRAL_HUB_API_URL TRUST_API_KEY_HEADER FL_BACKEND \
       FLOWER_KIT_DATE FLARE_KIT_DATE DOCKER_TAG DOCKER_REGISTRY \
       DOCKER_FL_TAG DOCKER_FL_REGISTRY NLB_SUBDOMAIN FL_SERVER_PORT
# xnat/Makefile's dev-only plugin cache reads this to reach the artifacts bucket. Unexported it is
# empty in the `make -C trust up-trust` path (the root Makefile blanket-exports the hub env, so only
# a top-level `make up` would carry it), and the cache download fails on a bucket-less S3 URI.
export FLIP_ARTIFACTS_BUCKET_NAME
# UID/GID/UNAME come from the same hub .env and must be exported for the same reason:
# the dev compose runs imaging-api, data-access-api and trust-api as `user: "${UID}:${GID}"`
# so they can write host-owned bind mounts (notably BASE_IMAGES_DOWNLOAD_DIR, which this
# Makefile mkdir's as the host user). Unexported they interpolate to "", compose renders
# `user: ":"`, the containers fall back to image root — and since they also run CapDrop=ALL
# (no CAP_DAC_OVERRIDE) that root cannot write a host-user-owned dir. The visible symptom is
# every FL image download failing 500 and training dying with num_samples=0.
export UID GID UNAME
endif

include ../deploy/fl_backend.mk

# Prefix for the hub-shared network names, derived from FLIP_INSTANCE (FLIP#957).
# `?=` so the root Makefile's exported value wins when invoked as `make -C trust`,
# while a standalone `make -C trust ...` still resolves it from FLIP_INSTANCE.
# Unset → empty → the original network names.
HUB_NET_PREFIX ?= $(if $(FLIP_INSTANCE),$(FLIP_INSTANCE)-,)
export HUB_NET_PREFIX

# Host gid for group_add on FL containers reading host-provisioned 640 certs/keys (dev).
export DOCKER_GID := $(shell id -g)

COMMON_COMPOSE_FILE := deploy/compose_trust.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := deploy/compose_trust.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Resolve FL_PROVISIONED_DIR (from .env) to an absolute path relative to the repo root.
# Docker requires absolute paths for volume mounts; the .env value may be relative.
# Skip if already absolute (the parent Makefile resolves it before invoking sub-make).
MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ifeq ($(patsubst /%,,$(FL_PROVISIONED_DIR)),$(FL_PROVISIONED_DIR))
override FL_PROVISIONED_DIR := $(abspath $(MAKEFILE_DIR)/../$(FL_PROVISIONED_DIR))
endif

OMOP_DB_CMD=make -C ./omop-db
ORTHANC_CMD=make -C ./orthanc
# --project-directory . keeps relative paths inside the compose files (./trust-api,
# ./observability/, ./omop-db/volumes/, …) resolving from trust/ even though the compose
# files now live in trust/deploy/. Avoids having to rewrite every ./X to ../X.
# --env-file <kit> hands compose the kit file so ${POLL_INTERVAL_SECONDS},
# ${PACS_ID}, ${OMOP_POSTGRES_*}, etc. (all the kit's Trust-local creds +
# tunables that aren't in TRUST_VARS) resolve at YAML-substitution time.
# Without it compose silently uses empty strings → trust-api/imaging-api
# boot fails with pydantic int-parse errors on POLL_INTERVAL_SECONDS /
# PACS_ID. Build/test targets that don't set KIT leave the flag empty
# (compose tolerates absence of --env-file just fine).
__ENV_FILE_FLAG := $(if $(wildcard $(KIT_FILE)),--env-file $(KIT_FILE),)
DOCKER_COMPOSE_CMD=docker compose $(__ENV_FILE_FLAG) --project-directory . -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose $(__ENV_FILE_FLAG) --project-directory . -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f deploy/compose_trust.debug.override.yml

# UP_PULL_FLAGS controls the pull/build behaviour of the up-trust targets.
#
# Default: repo-built services (trust-api, imaging-api, data-access-api, orthanc)
# carry `pull_policy: always` in compose_trust.development.yml, so they always
# pull from GHCR on their own. This flag only adds the global `--pull always`
# that keeps FL images fresh — dropped when DOCKER_FL_REGISTRY is empty (local
# `:dev` FL images, e.g. flare-fl-client:dev), since docker can't pull an
# unpublished manifest and `--pull always` would error.
#
# BUILD=true: rebuild the repo-built services from source instead of pulling.
# `--build` forces the build; `--pull missing` overrides the per-service
# `pull_policy: always` for this run so the fresh build isn't re-pulled away.
# Propagated automatically from `make up BUILD=true` at the repo root.
ifeq ($(BUILD),true)
UP_PULL_FLAGS=--build --pull missing
else ifneq ($(strip $(DOCKER_FL_REGISTRY)),)
UP_PULL_FLAGS=--pull always
else
UP_PULL_FLAGS=
endif

# ── Per-trust deployment ──────────────────────────────────────────────────
# A trust stack is selected by KIT — the kit-file name under trust/.env.<KIT>
# (the FL kit slot name, e.g. Trust_1). trust/.env.<KIT> carries that trust's
# host-local profile (ports, bind dirs), trust-local credentials, hub-shared
# block, and kit credentials. `make register-trusts` writes the hub-managed
# blocks, trust/.env.<KIT>.example is the template the operator copies first.
# KIT has no default so non-KIT targets (build, test) don't spuriously
# -include a kit file — pass KIT=<name> explicitly. The kit file itself is
# `-include`d at the top of this Makefile so FL_BACKEND (hub-shared) lands
# before fl_backend.mk consumes it.
#
# TRUST_VARS now only carries values that aren't in the kit file: ones
# derived from $(KIT) at make-time. Every kit-file key (host-local ports
# + bind dirs, trust-local credentials, hub-shared block, kit credentials)
# now flows into docker compose via the --env-file flag baked into
# DOCKER_COMPOSE_CMD above. The kit file is authoritative — operators
# customise paths/ports there, not via make-side overrides — so the
# previous PROD-keyed fallbacks and per-key passthroughs are gone.

# Numeric suffix of the ASSIGNED FL kit slot (Trust_1 -> 1), read from the
# included kit file — drives both the per-trust overlay network
# (deploy_trust-network-<N>) and the compose project name (trust<N>), preserving
# the existing scheme. Decoupled from the kit FILENAME (now the trust CODE) so
# the slot the hub assigned governs the runtime topology, not the handle.
TRUST_NUM := $(FL_KIT_SLOT_NUMBER)
TRUST_PROJECT := trust$(TRUST_NUM)
# Dev only: expose this trust's APIs on the host. The override binds a single
# fixed host-port set, so only the trust assigned slot Trust_1 claims it.
TRUST_OVERRIDE := $(if $(filter Trust_1,$(FL_KIT_SLOT)),-f deploy/compose_trust-1_override.yml,)

# GPU passthrough: apply the device-reservation overlay (compose_trust.<env>.gpu.yml)
# only when the kit declares NUM_AVAILABLE_GPUS > 0. A GPU-less host (the AWS EC2
# t3.xlarge test trust, or a CPU-only dev/CI box) leaves it 0/unset, so the overlay
# is skipped and the fl-client comes up CPU-only instead of failing with "could not
# select device driver". The same value feeds the fl-client app's own GPU hint, so
# Docker exposure and the app stay in lockstep. Mirrors the TRUST_OVERRIDE pattern.
GPU_OVERRIDE := $(if $(filter-out 0,$(strip $(NUM_AVAILABLE_GPUS))),-f deploy/compose_trust.$(__DCKR_SUFFIX).gpu.yml,)
GPU_STATUS_MSG := $(if $(GPU_OVERRIDE),🖥️  GPU passthrough on — fl-client will reserve $(NUM_AVAILABLE_GPUS) NVIDIA GPU(s),⚠️  fl-client starting CPU-only — NUM_AVAILABLE_GPUS=$(or $(strip $(NUM_AVAILABLE_GPUS)),unset). Set it >0 on a GPU host to enable passthrough.)

# TRUST_NAME (passed to trust-api for display/logging) prefers the kit's
# registered name, falling back to the assigned slot then the CODE so a
# not-yet-migrated kit (no TRUST_NAME) still resolves. nvflare's FL-material
# path and flower's SUPERNODE_NAME key on ${FL_KIT_SLOT} (the slot), not this.
# Double-quoted because a registered name may contain spaces AND apostrophes
# (e.g. "Guy's and St Thomas' Trust") — unquoted it splits in the recipe's
# `VAR=val cmd` shell prefix ("EC2: not found"); single-quoted it breaks on the
# apostrophe. Double quotes handle both (trust names never contain $/backtick/").
TRUST_VARS = \
	TRUST_NETWORK_NAME=deploy_trust-network-$(TRUST_NUM) \
	TRUST_NUMBER=$(TRUST_NUM) \
	TRUST_NAME="$(or $(TRUST_NAME),$(FL_KIT_SLOT),$(KIT))"

# Non-KIT targets such as `make build` do not load a trust kit, but Docker
# Compose still validates bind mounts while building images.
TRUST_BUILD_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=$(or $(BASE_IMAGES_DOWNLOAD_DIR),./data/trust) \
	ORTHANC_STORAGE_DIR=$(or $(ORTHANC_STORAGE_DIR),./orthanc/orthanc-storage-trust1) \
	FL_KIT_SLOT=$(or $(FL_KIT_SLOT),Trust_1) \
	FL_KIT_SLOT_NUMBER=$(or $(FL_KIT_SLOT_NUMBER),1)

# Guard: refuse to bring a trust up if its OMOP DB data dir is empty or missing.
# Docker would otherwise silently auto-create an empty bind mount on first start,
# and the omop-db image's init scripts (which create the schema + read-only
# data_analyst_reader role) only run when the data dir is empty AT THE POSTGRES
# DATA LEVEL — a freshly-created host dir does NOT trigger init because Postgres
# treats the missing PG_VERSION file as "broken", not "uninitialized". The
# downstream symptom is cohort queries failing with "FATAL: password
# authentication failed for user data_analyst_reader" hours later.
#
# Pass the resolved path (matching what the compose file will mount) as $1.
define check_omop_data_dir
	@dir="$(1)"; \
	owner="$$(stat -c %u "$$dir" 2>/dev/null)"; \
	if [ -f "$$dir/PG_VERSION" ] || { [ -n "$$owner" ] && [ "$$owner" != "$$(id -u)" ]; }; then populated=1; else populated=0; fi; \
	if [ "$$populated" -eq 0 ]; then \
	    echo "❌ OMOP DB data dir is empty or missing: $$dir"; \
	    echo "   The omop-db container would start with no schema and no"; \
	    echo "   data_analyst_reader role — cohort queries would fail at runtime."; \
	    echo ""; \
	    echo "   Populate it first:"; \
	    echo "       make -C trust update-omop-data"; \
	    echo ""; \
	    echo "   Or, for a joining trust whose friendly name doesn't match a"; \
	    echo "   canonical data dir, point OMOP_DATA_DIR at an existing populated"; \
	    echo "   path (e.g. OMOP_DATA_DIR=./omop-db/volumes/Trust_2/db_data)."; \
	    exit 1; \
	fi
endef

# Guard: any KIT-selected target needs KIT set (it derives the project name,
# network and kit-file path).
define require_kit
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi
endef

# Guard: bringing a trust UP additionally needs its kit file (ports + credentials).
# Teardown (down-trust) only needs the project name, so it uses require_kit instead.
# Placeholder check: refuse to start a stack with `<run-make-…>` placeholders still
# in the kit file. Without this, an unfilled value like FL_KIT_SLOT=<run-make-register-trusts>
# would flow into the docker compose command line as TRUST_NAME=<…> and the shell
# would parse `<` as a stdin redirect (cryptic "No such file or directory" crash).
define require_kit_file
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi; \
	if [ ! -f "$(KIT_FILE)" ]; then \
	    echo "❌ Kit file trust/$(KIT_FILE) not found."; \
	    echo "   Copy trust/$(KIT_FILE).example to trust/$(KIT_FILE), then run"; \
	    echo "   'make register-trusts' (from the repo root) to fill in the credentials."; \
	    exit 1; \
	fi; \
	if grep -qE '^[A-Z0-9_]+=<run-make-' $(KIT_FILE); then \
	    n=$$(grep -cE '^[A-Z0-9_]+=<run-make-' $(KIT_FILE)); \
	    echo "❌ Kit file trust/$(KIT_FILE) still has $$n unfilled <run-make-…> placeholder(s)."; \
	    echo "   Local hub: run 'make register-trusts' (from the repo root)."; \
	    echo "   On-prem trust against a remote hub:"; \
	    echo "     run 'make onboard-onprem-trust KIT=$(KIT)' (from the repo root)"; \
	    echo "     for the onboarding steps; if only <run-make-sync-trust-kit> values remain,"; \
	    echo "     ask the hub admin to re-send the kit (it's been resynced)."; \
	    exit 1; \
	fi
endef

build:
	$(TRUST_VARS) $(TRUST_BUILD_VARS) ${DOCKER_COMPOSE_CMD} -p $(or $(TRUST_PROJECT),trust) build

# Bring up the trust stack selected by KIT (e.g. KIT=Trust_1). Reads
# trust/.env.<KIT> for that trust's ports / dirs / credentials. The
# update-omop-data / update-orthanc-data prereqs pull mock OMOP / Orthanc
# fixtures from the dev source — fine for dev / hybrid testing.
# Pull/build behaviour is governed by $(UP_PULL_FLAGS) (defined in the
# root Makefile and inherited here): pulls fresh FL images when
# DOCKER_FL_REGISTRY is set, builds from source on BUILD=true, no-op
# otherwise.
# Refuse to start when the compose project this kit resolves to is already running a
# DIFFERENT trust. TRUST_PROJECT is trust$(FL_KIT_SLOT_NUMBER), and slots are handed
# out by the hub from its own database — so a second hub (FLIP_INSTANCE, FLIP#957)
# starts again at 1 and its first trust lands on a slot the default stack is already
# using. Without this, compose treats the running containers as part of the project it
# was asked to bring up and recreates them, destroying a live trust.
#
# The owning kit is read off compose's own environment_file label, compared by file
# name so re-running for the SAME kit (the normal restart/update path) still works,
# including from another checkout.
define check_slot_not_taken
	@occupied=$$(docker ps --filter "label=com.docker.compose.project=$(TRUST_PROJECT)" \
		--format '{{.Label "com.docker.compose.project.environment_file"}}' 2>/dev/null | head -1); \
	if [ -n "$$occupied" ] && [ "$$(basename "$$occupied")" != "$(KIT_FILE)" ]; then \
		echo "❌ FL kit slot $(TRUST_NUM) is already in use on this host."; \
		echo "   Compose project '$(TRUST_PROJECT)' is running trust $$(basename "$$occupied"),"; \
		echo "   but you asked to start $(KIT_FILE). Bringing this up would hand those"; \
		echo "   containers to the wrong kit and recreate them, taking the running trust down."; \
		echo ""; \
		echo "   Slots are assigned per-hub, so a second stack (FLIP_INSTANCE) restarts at 1"; \
		echo "   and collides with the default stack. Pick a free slot:"; \
		echo "       docker ps --format '{{.Label \"com.docker.compose.project\"}}' | sort -u"; \
		echo "   then set FL_KIT_SLOT_NUMBER=<free> in trust/$(KIT_FILE) and re-run."; \
		exit 1; \
	fi
endef

up-trust: update-omop-data update-orthanc-data create-networks
	$(require_kit_file)
	@echo "🚢 Starting Trust services (KIT=$(KIT))..."
	@echo "⚙️ Using kit file trust/$(KIT_FILE)"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(call check_omop_data_dir,$(or $(OMOP_DATA_DIR),./omop-db/volumes/Trust_$(TRUST_NUM)/db_data))
	$(check_slot_not_taken)
	mkdir -p $(or $(BASE_IMAGES_DOWNLOAD_DIR),./data/$(KIT))
	@echo "$(GPU_STATUS_MSG)"
	$(TRUST_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} $(GPU_OVERRIDE) $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) up -d $(UP_PULL_FLAGS)
	@echo "🩻 Starting XNAT for $(KIT)..."
	$(MAKE) -C xnat up-xnat KIT=$(KIT) PROD=$(PROD)

down-trust:
	$(require_kit)
	@echo "🛑 Stopping Trust services (KIT=$(KIT))..."
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) down --remove-orphans
	@echo "🩻 Stopping XNAT for $(KIT)..."
	$(MAKE) -C xnat down-xnat KIT=$(KIT) PROD=$(PROD)

restart-trust: down-trust up-trust

# EC2 variant — no host-port override, --pull always. Networks and OMOP data
# are provisioned on the instance by deploy/providers/AWS/site.yml, so the
# update-omop-data / create-networks prerequisites are intentionally omitted.
# The EC2 t3.xlarge is GPU-less, so the fl-client is pinned to CPU regardless of
# the kit's NUM_AVAILABLE_GPUS. Two mechanisms must BOTH be neutralised:
#   1. GPU_OVERRIDE (the docker GPU-passthrough overlay) is omitted — otherwise
#      `up` fails with "could not select device driver".
#   2. NUM_AVAILABLE_GPUS is forced to 0 in the env prefix (shell env beats the
#      kit's --env-file) — otherwise the fl-client's generated nvflare
#      resources.json keeps num_of_gpus=1 and the client dies at startup with
#      "num_of_gpus specified (1) exceeds available GPUs: 0". Omitting the
#      overlay alone (mechanism 1) does NOT cover this.
# Together these defend the deploy against a kit seeded NUM_AVAILABLE_GPUS=1 (the
# template default). A GPU-equipped EC2 instance would drop the override and add
# $(GPU_OVERRIDE) back.
up-trust-ec2:
	$(require_kit_file)
	@echo "🚢 Starting Trust services on EC2 (KIT=$(KIT))..."
	@echo "⚙️ Using kit file trust/$(KIT_FILE)"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	@echo "🖥️  EC2 trust runs CPU-only by design — GPU overlay omitted + NUM_AVAILABLE_GPUS forced to 0 (t3.xlarge has no GPU)."
	$(TRUST_VARS) NUM_AVAILABLE_GPUS=0 DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -p $(TRUST_PROJECT) up -d --pull always
	@echo "🩻 Starting XNAT for $(KIT)..."
	$(MAKE) -C xnat up-xnat KIT=$(KIT) PROD=$(PROD)

down-trust-ec2:
	$(require_kit)
	@echo "🛑 Stopping Trust services on EC2 (KIT=$(KIT))..."
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} -p $(TRUST_PROJECT) down --remove-orphans
	@echo "🩻 Stopping XNAT for $(KIT)..."
	$(MAKE) -C xnat down-xnat KIT=$(KIT) PROD=$(PROD)

# Dev convenience: bring up every registered dev trust. The kit files
# (trust/.env.<CODE>.$(ENV), written by `make register-trusts`) ARE the list —
# each is a separate sub-make so its `-include` loads only that trust's kit.
up: create-networks
	@found=0; for f in .env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#.env.}; kit=$${kit%.$(ENV)}; found=1; \
	  $(MAKE) up-trust KIT=$$kit || { echo "❌ Failed to start trust $$kit"; exit 1; }; \
	done; \
	[ "$$found" = 1 ] || echo "ℹ️  No trust/.env.*.$(ENV) kits found — run 'make register-trusts' (from repo root) first."

down:
	@for f in .env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#.env.}; kit=$${kit%.$(ENV)}; \
	  $(MAKE) down-trust KIT=$$kit; \
	done

# Start / stop only the FL client containers, leaving the rest of each trust stack up.
# Each dev trust project (trust1, trust2) runs a client per FL net, hence both
# net-1 and net-2. Per-trust delegation uses the same KIT machinery as up-trust:
# the sub-make's `-include ../.env.<KIT>` re-resolves TRUST_VARS / TRUST_PROJECT /
# TRUST_OVERRIDE for that kit alone, so Trust_1 picks up its host-port override
# and Trust_2 stays on the network ports from its kit file.
up-fl-clients:
	@echo "🚢 Starting FL clients..."
	@for f in .env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#.env.}; kit=$${kit%.$(ENV)}; \
	  $(MAKE) up-fl-clients-kit KIT=$$kit; \
	done

up-fl-clients-kit:
	$(require_kit_file)
	@echo "$(GPU_STATUS_MSG)"
	$(TRUST_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} $(GPU_OVERRIDE) $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) up -d --no-deps $(UP_PULL_FLAGS) fl-client-net-1 fl-client-net-2

down-fl-clients:
	@echo "🛑 Stopping and removing FL clients..."
	@for f in .env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#.env.}; kit=$${kit%.$(ENV)}; \
	  $(MAKE) down-fl-clients-kit KIT=$$kit; \
	done

down-fl-clients-kit:
	$(require_kit)
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) rm -fs fl-client-net-1 fl-client-net-2

update-omop-data:
	$(OMOP_DB_CMD) update-omop-data TRUST=$(TRUST)

update-orthanc-data:
	$(ORTHANC_CMD) update-orthanc-data TRUST=$(TRUST)

tests:
	cd data-access-api && make test && \
	cd ../imaging-api && make test && \
	cd ../trust-api && make test

create-networks-core:
	@echo "🔧 Ensuring external Docker networks exist..."
	@{ docker network inspect $(HUB_NET_PREFIX)central-hub-trust-apis-network >/dev/null 2>&1 || docker network create --driver bridge $(HUB_NET_PREFIX)central-hub-trust-apis-network >/dev/null || docker network inspect $(HUB_NET_PREFIX)central-hub-trust-apis-network >/dev/null 2>&1 || { echo "❌ Could not create Docker network $(HUB_NET_PREFIX)central-hub-trust-apis-network — see the daemon error above."; exit 1; }; }
	@{ docker network inspect $(HUB_NET_PREFIX)deploy_shared-net-1 >/dev/null 2>&1 || docker network create --driver bridge $(HUB_NET_PREFIX)deploy_shared-net-1 >/dev/null || docker network inspect $(HUB_NET_PREFIX)deploy_shared-net-1 >/dev/null 2>&1 || { echo "❌ Could not create Docker network $(HUB_NET_PREFIX)deploy_shared-net-1 — see the daemon error above."; exit 1; }; }
	@{ docker network inspect $(HUB_NET_PREFIX)deploy_shared-net-2 >/dev/null 2>&1 || docker network create --driver bridge $(HUB_NET_PREFIX)deploy_shared-net-2 >/dev/null || docker network inspect $(HUB_NET_PREFIX)deploy_shared-net-2 >/dev/null 2>&1 || { echo "❌ Could not create Docker network $(HUB_NET_PREFIX)deploy_shared-net-2 — see the daemon error above."; exit 1; }; }

create-networks-trust-1:
	@{ docker network inspect deploy_trust-network-1 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-1; }

create-networks-trust-2:
	@{ docker network inspect deploy_trust-network-2 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-2; }

create-networks: create-networks-core create-networks-trust-1 create-networks-trust-2

# deploy_trust-network-* are keyed by trust number, NOT by FLIP_INSTANCE, so every
# instance shares them. Removing them while tearing down a secondary instance would
# delete the default stack's trust networks — today docker happens to refuse (they
# are in use), but that is luck, not a guard: with the trusts down it would succeed
# and take the default stack's networks with it. Hence the prefix check below.
remove-networks:
	@echo "🗑️  Removing all trust networks..."
	@docker network rm $(HUB_NET_PREFIX)central-hub-trust-apis-network 2>/dev/null || true
	@docker network rm $(HUB_NET_PREFIX)deploy_shared-net-1 2>/dev/null || true
	@docker network rm $(HUB_NET_PREFIX)deploy_shared-net-2 2>/dev/null || true
	@if [ -z "$(HUB_NET_PREFIX)" ]; then \
		docker network rm deploy_trust-network-1 2>/dev/null || true; \
		docker network rm deploy_trust-network-2 2>/dev/null || true; \
	else \
		echo "  ↩️  keeping deploy_trust-network-{1,2} — shared across instances, not owned by '$(HUB_NET_PREFIX)'"; \
	fi
	@echo "✅ Trust networks removed!"

recreate-networks: remove-networks create-networks
	@echo "✅ All trust networks recreated with swarm support!"

# First registered dev trust — the default KIT for the debug targets when none
# is given (pass KIT=<CODE> to debug a specific trust).
FIRST_DEV_KIT := $(firstword $(patsubst .env.%.$(ENV),%,$(wildcard .env.*.$(ENV))))

# Debug targets operate on one trust stack (KIT=<CODE>, default the first dev
# kit). They recurse so the sub-make's `-include` loads that kit (the -include
# is parse-time, so KIT must be set at make-invocation, not target-local).
debug:
	$(MAKE) DEBUG=true restart-trust KIT=$(or $(KIT),$(FIRST_DEV_KIT))
debug-off:
	$(MAKE) DEBUG=false restart-trust KIT=$(or $(KIT),$(FIRST_DEV_KIT))
debug-data-access-api:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=true SVC=data-access-api
debug-imaging-api:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=true SVC=imaging-api
debug-trust-api:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=true SVC=trust-api
debug-data-access-api-off:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=false SVC=data-access-api
debug-imaging-api-off:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=false SVC=imaging-api
debug-trust-api-off:
	$(MAKE) _debug-service KIT=$(or $(KIT),$(FIRST_DEV_KIT)) DEBUG=false SVC=trust-api

# Internal helper — brings one service up under the debug compose override.
_debug-service:
	DEBUG=$(DEBUG) $(TRUST_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p $(TRUST_PROJECT) up --remove-orphans -d $(SVC)


# Trust-side integration tests (issue #369, B3). Each delegated target brings up
# the shared trust/deploy/compose.test.yml stack via Testcontainers — run them serially
# so the per-suite session fixtures don't fight over Docker resources.
integration_test:
	$(MAKE) -C data-access-api integration_test
	$(MAKE) -C trust-api integration_test
