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

.PHONY: build build-fl dev prod clean stop up down up-no-trust up-trusts central-fl central-hub \
		restart restart-fl restart-no-trust ci tests debug create-networks remove-networks recreate-networks consolidate-deps \
		check-aws-access generate-internal-service-key generate-xnat-credentials \
		register-trust register-trusts new-trust _wait-for-hub integration_test \
		sync-trust-kit sync-trust-kits lock \
		deploy-trust-k8s undeploy-trust-k8s \
		demo-video demo-users seed-demo-projects

ifeq ($(PROD),true)
MAIN_ENV_FILE=.env.production
__DCKR_SUFFIX=production
ENV=production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=.env.stag
__DCKR_SUFFIX=production
ENV=stag
else
MAIN_ENV_FILE=.env.development
__DCKR_SUFFIX=development
ENV=development
endif

# Print which environment files are being used
$(info Using MAIN_ENV_FILE: $(MAIN_ENV_FILE))

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

include deploy/fl_backend.mk

# Host gid for group_add on FL containers reading host-provisioned 640 certs/keys (dev).
export DOCKER_GID := $(shell id -g)

COMMON_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# ── Parallel dev stacks (FLIP#957) ────────────────────────────────────────
# FLIP_INSTANCE names a hub instance so a second dev stack can run alongside the
# default one. Unset (the norm) every derived name is byte-identical to what it
# was before this knob existed, so existing deployments are untouched.
#
# HUB_NET_PREFIX is also read by trust/Makefile's network targets — it is
# exported so `$(MAKE) -C trust` sees the same value.
#
# COMPOSE_PROJECT matters as much as the names themselves: compose derives the
# project from the directory of the first -f file, which is always `deploy/`,
# so without this BOTH stacks would land in one project (`deploy`) regardless of
# which checkout they were launched from, and `up` on one would reconcile — and
# tear down — the other's containers. `-p deploy` is exactly the implicit value
# used today, so pinning it changes nothing for the default stack.
export HUB_NET_PREFIX := $(if $(FLIP_INSTANCE),$(FLIP_INSTANCE)-,)
COMPOSE_PROJECT ?= $(HUB_NET_PREFIX)deploy

# Resolve FL_PROVISIONED_DIR / FL_JOBS_DIR (from .env, or overridden at the CLI) to
# absolute paths, since docker requires those for volume mounts.
#
# Only prepend MAKEFILE_DIR when the value is not already absolute. $(abspath) merely
# normalises the string it is given — it does not notice the operand was already
# rooted — so joining unconditionally welded two absolute paths together:
#   FL_PROVISIONED_DIR=/opt/kits  ->  /path/to/repo/opt/kits
# which surfaced as a misleading "workspace not provisioned" naming a path the caller
# never asked for, even though CLAUDE.md advertises the CLI override.
MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
abs_or_relative_to_repo = $(abspath $(if $(filter /%,$(1)),$(1),$(MAKEFILE_DIR)/$(1)))
override FL_PROVISIONED_DIR := $(call abs_or_relative_to_repo,$(FL_PROVISIONED_DIR))
override FL_JOBS_DIR := $(call abs_or_relative_to_repo,$(FL_JOBS_DIR))

# Service configuration
define SERVICE_CONFIG
data-access-api:trust
imaging-api:trust
trust-api:trust
flip-api
fl-api
endef

# Function to get service type (trust or central)
get_service_type = $(word 2,$(subst :, ,$(filter $1:%,$(SERVICE_CONFIG))))

# Function to get service display name
get_service_name = $(subst -api,, $(subst flip-,central hub ,$(subst fl-,central FL ,$1)))

export COMPOSE_BAKE=true
DOCKER_COMMAND=docker compose -p $(COMPOSE_PROJECT) -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose -p $(COMPOSE_PROJECT) -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f deploy/compose.development.debug.override.yml
SHOW_LOGS_CENTRAL_HUB=docker logs -f $(HUB_NET_PREFIX)flip-api --tail 100 --timestamps --follow
GENERIC_LOGS=docker logs -f --tail 100 --timestamps --follow

# UP_PULL_FLAGS controls the pull/build behaviour of the `up` targets.
#
# Default: repo-built services (flip-api + trust APIs + orthanc) carry
# `pull_policy: always` in the dev compose, so they always pull from GHCR on
# their own. This flag only adds the global `--pull always` that keeps FL images
# fresh — and it's dropped when DOCKER_FL_REGISTRY is empty (local `:dev` FL
# images, e.g. flare-fl-server:dev), because docker can't pull a manifest that
# isn't published and `--pull always` would error.
#
# BUILD=true: rebuild the repo-built services from source instead of pulling.
# `--build` forces the build; `--pull missing` overrides the per-service
# `pull_policy: always` for this run so the fresh build isn't immediately
# re-pulled away, while still fetching any absent FL/infra image.
ifeq ($(BUILD),true)
UP_PULL_FLAGS=--build --pull missing
else ifneq ($(strip $(DOCKER_FL_REGISTRY)),)
UP_PULL_FLAGS=--pull always
else
UP_PULL_FLAGS=
endif

# Build the Docker images
build:
	@echo "🛠️ Building Docker images..."
	@echo "UI_PORT = $(UI_PORT)"
	${DOCKER_COMMAND} build --no-cache
	$(MAKE) -C trust build
	$(MAKE) -C trust/xnat build
	@echo "✅ Docker images built successfully!"

# Build the NVFLARE FL service images locally, tagged :dev, for iterating on fl-services/
# or the flip-utils `flip` package before pushing. The deploy compose pulls FL images from
# GHCR (DOCKER_FL_TAG), so they are NOT built by `make build`; this uses the build defs in
# fl-services/<backend>/compose.dev.yml. fl-base must build first, in its own invocation: a plain
# `FROM flare-fl-base` in the server/client/api Dockerfiles is invisible to Compose's build
# graph, so a single `compose build` would build the derived images against a stale base.
# fl-base's build-only profile keeps it out of `up`; the derived images pull it via BASE_REF.
# Then run the stack on the freshly-built images with:
#   make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
# build-fl forwards to the selected backend's Makefile (fl-services/<backend>/Makefile),
# mirroring the fl-tutorials/ forwarder. Standalone FL run/provision/submit targets are
# deliberately NOT mirrored here — run them in the backend dir, which is where FL
# deployments are tested: make -C fl-services/<backend> {build,provision,up,down,submit}.
build-fl:
	@$(MAKE) -C fl-services/$(FL_BACKEND) build LOCAL_DEV=$(LOCAL_DEV)
	@echo "✅ FL :dev images built. Run them with: make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev"

# Run all services
# Pull/build behaviour is governed by $(UP_PULL_FLAGS): pulls fresh FL images
# when DOCKER_FL_REGISTRY is set, builds from source on BUILD=true, no-op otherwise.
up: check-aws-access generate-internal-service-key create-networks _ensure-fl-jobs-dir _check-fl-provisioned
	@echo "🚢 Starting all services..."
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d $(UP_PULL_FLAGS)
	@echo "🔑 Registering trusts and writing kit files..."
	$(MAKE) register-trusts
	@echo "🚢 Starting trust services (each trust brings up its own XNAT)..."
	$(MAKE) -C trust up
	@echo "✅ All services started successfully!"

# Ensure jobs/<net> dirs exist with writable perms before starting containers,
# so Docker doesn't create them root-owned and lock out the fl-api container
# (which runs as a non-root `app` user).
#
# Net IDs come from NET_ENDPOINTS (the per-env single source of truth — prod
# has only net-1; dev has net-1 + net-2). A new net is just an NET_ENDPOINTS
# entry plus a compose service block; this target picks it up automatically.
# Both backends use jobs/<net>: Flower stores uploaded app bundles there; NVFLARE
# stages de-bundled evaluation checkpoints there for the fl-server to load from disk.
_ensure-fl-jobs-dir:
	@if [ "$(FL_BACKEND)" = "flower" ] || [ "$(FL_BACKEND)" = "nvflare" ]; then \
		command -v jq >/dev/null 2>&1 || { echo "❌ _ensure-fl-jobs-dir: jq not found on PATH; required to parse NET_ENDPOINTS" >&2; exit 1; }; \
		nets=$$(printf '%s' '$(NET_ENDPOINTS)' | jq -r 'keys_unsorted | join(" ")' 2>/dev/null) || nets=""; \
		if [ -z "$$nets" ]; then \
			echo "❌ _ensure-fl-jobs-dir: NET_ENDPOINTS is empty or unparseable; cannot derive net IDs" >&2; \
			exit 1; \
		fi; \
		mkdir -p jobs; \
		chmod 777 jobs; \
		for net in $$nets; do \
			mkdir -p jobs/$$net; \
			chmod 777 jobs/$$net; \
		done; \
	fi

# Fail fast (NVFLARE) when the per-net startup kits are missing — delegated to
# scripts/check-fl-provisioned.sh (see that script for the why/how). Net IDs
# come from NET_ENDPOINTS (same source as _ensure-fl-jobs-dir); the check is a no-op
# for non-NVFLARE backends.
_check-fl-provisioned:
	@FL_BACKEND='$(FL_BACKEND)' NET_ENDPOINTS='$(NET_ENDPOINTS)' FL_PROVISIONED_DIR='$(FL_PROVISIONED_DIR)' \
		scripts/check-fl-provisioned.sh

# Minimal $(MAKE) up
up-no-trust: generate-internal-service-key create-networks _ensure-fl-jobs-dir _check-fl-provisioned
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d $(UP_PULL_FLAGS)

up-trusts: create-networks
	@echo "🔑 Registering trusts and writing kit files (hub must already be up)..."
	$(MAKE) register-trusts
	@echo "🚢 Starting Trust services (each trust brings up its own XNAT)..."
	$(MAKE) DEBUG=$(DEBUG) -C trust up
	@echo "✅ Trust services started successfully!"

up-trust-ec2: create-networks
	@echo "Hey! PROD="$(PROD)
	@echo "Hey! UI_PORT="$(UI_PORT)
	@echo "🚢 Starting Trust services..."
	$(MAKE) DEBUG=$(DEBUG) -C trust up-trust-ec2 KIT=$(KIT) PROD=${PROD}
	@echo "✅ Trust services started successfully!"

central-hub: create-networks-centralhub
	$(MAKE) -C flip-api up

# On-prem operator flow — start a trust on the local host pointing at a
# remote hub (typically the prod CloudFront one).
#
# Gated on the onboard-onprem-trust checklist (kit file present, swarm
# initialized, Hub-shared + Kit credentials filled in, FL_KIT_DIR exists +
# contains the expected files). On any failure the checklist prints what's
# missing and how to fix it; we exit so the operator doesn't hit a cryptic
# compose / pydantic failure deeper in the stack.
up-onprem-trust:
	@[ -n "$(KIT)" ] || (echo "❌ KIT=<slot> is required (e.g. KIT=Trust_2)"; exit 1)
	@$(MAKE) onboard-onprem-trust KIT=$(KIT)
	$(MAKE) DEBUG=$(DEBUG) -C trust up-trust KIT=$(KIT) PROD=$(or $(PROD),true)

# Symmetric down for the on-prem flow. Wraps trust/Makefile's down-trust
# so an operator doesn't have to remember the -C trust path or PROD value.
down-onprem-trust:
	@[ -n "$(KIT)" ] || (echo "❌ KIT=<slot> is required (e.g. KIT=Trust_2)"; exit 1)
	$(MAKE) -C trust down-trust KIT=$(KIT) PROD=$(or $(PROD),true)

# Readiness checklist for an on-prem trust. Prints the operator's public
# IP + a row per precondition (kit file, swarm, Hub-shared block, Kit
# credentials, FL_KIT_DIR and its contents) with ✅/❌ and a concrete fix
# under each failing row. Exits 0 when all checks pass (the operator can
# then run `make up-onprem-trust KIT=<slot>`), 1 otherwise.
#
# Invoked automatically as a precheck by up-onprem-trust; can also be run
# standalone (e.g. `make onboard-onprem-trust KIT=Trust_2`).
#
# On-prem onboarding splits like this:
#   - Admin owns: opening the prod NLB to the operator's IP, UI-registering
#     the trust on the prod hub (Add-Trust modal mints the 5 Kit credentials,
#     admin pastes into trust/.env.<KIT>), running `make sync-trust-kit-N`
#     to populate the Hub-shared block in trust/.env.<KIT>, running
#     `make package-onprem-trust-kit` to tarball the populated env file
#     plus the operator's slice of the FL participant kit S3 bucket.
#   - Operator owns: extracting the tarball, copying the .env.<KIT> in,
#     setting FL_KIT_DIR + Host-local profile, running `make up-onprem-trust`.
# The operator never touches the prod UI directly — the admin uses it on
# the operator's behalf because Hub-shared values + FL kit S3 slice both
# need prod AWS creds the operator doesn't have.
onboard-onprem-trust:
	@uv run --no-config scripts/onboard_onprem_trust.py $(KIT)

# Stop all containers
down:
	@echo "🛑 Stopping all services..."
	$(MAKE) -C trust down

	${DOCKER_COMMAND} down --remove-orphans
	@echo "🛌 All services stopped successfully!"

# Clean Docker resources
clean:
	${DOCKER_COMMAND} down --rmi local && \
	docker system prune -f && \
	rm -rf ./flip-fl-api/*/transfer/*/

# Stop all services and remove the containers
restart: down up

# Restart only FL services (APIs, servers, and clients in trusts)
# NOTE: Uses $(UP_PULL_FLAGS) — pulls fresh FL images only when DOCKER_FL_REGISTRY is set
#       (same logic as `up`); an empty registry keeps locally built FL images.
# NOTE: Client keys must be re-registered before starting clients (Flower only)
# NOTE: flip-api is recreated first so its startup seeding re-applies FL_BACKEND onto the
#       FLNets rows — the seeded backend is canonical, so this is how a framework switch
#       (make restart-fl FL_BACKEND=...) takes effect. --no-deps leaves flip-db untouched.
restart-fl:
	@echo "🔄 Restarting FL services ($(FL_BACKEND))..."
	@echo "🔄 Step 1: Stopping and removing old FL clients..."
	$(MAKE) -C trust down-fl-clients
	@echo "🔄 Step 2: Recreating flip-api to re-seed FLNets.fl_backend=$(FL_BACKEND)..."
	${DOCKER_COMMAND} up -d --force-recreate --no-deps $(UP_PULL_FLAGS) flip-api
	@echo "🔄 Step 3: Restarting FL APIs and servers..."
	${DOCKER_COMMAND} up -d --force-recreate --no-deps $(UP_PULL_FLAGS) fl-api-net-1 fl-api-net-2 fl-server-net-1 fl-server-net-2
	@if [ "$(FL_BACKEND)" = "flower" ]; then \
		echo "🔄 Step 4: Registering new client keys with FL servers (Flower only)..."; \
		${DOCKER_COMMAND} up --force-recreate $(UP_PULL_FLAGS) register-supernode-keys-net-1 register-supernode-keys-net-2; \
	fi
	@echo "🔄 Step 5: Starting new FL clients..."
	$(MAKE) -C trust up-fl-clients
	@echo "✅ FL services restarted successfully!"

# Stop and start all services except the trust services related services
restart-no-trust:
	@echo "Debug mode: '${DEBUG}'"
	@echo "Passing DEBUG=${DEBUG} to the downstream $(MAKE) commands..."
	$(MAKE) -e DEBUG=$(DEBUG) -C flip-api restart
ci:
	act --env-file .env.development
ui:
ifeq ($(strip $(PROD)),)
	@echo "🚀 Starting UI..."
	$(DOCKER_COMMAND) up --remove-orphans -d flip-ui
else
	@echo "ℹ️  flip-ui is served from S3 + CloudFront when PROD=$(PROD); no container to start."
	@echo "    Run \`make -C deploy/providers/AWS deploy-ui PROD=$(PROD)\` to publish the bundle."
endif
ui-off:
ifeq ($(strip $(PROD)),)
	@echo "🛑 Stopping UI..."
	$(DOCKER_COMMAND) down --remove-orphans flip-ui
else
	@echo "ℹ️  No flip-ui container runs when PROD=$(PROD) (S3 + CloudFront)."
endif
tests:
	$(MAKE) -C flip-ui unit_test
	$(MAKE) -C flip-ui e2e_test
	$(MAKE) -C flip-api test

debug-all:
	@echo "🚨 Starting debug mode by overriding the DEBUG environment variable..."
	DEBUG=true $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d
	$(MAKE) -C trust debug
debug-off-all:
	@echo "🚨 Stopping debug mode by removing the DEBUG environment variable override..."
	$(MAKE) -C flip-api delete_testing_projects
	DEBUG=false $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d
	$(MAKE) -C trust debug-off

create-networks-centralhub:
	@{ docker network inspect $(HUB_NET_PREFIX)central-hub-network >/dev/null 2>&1 || docker network create --driver bridge $(HUB_NET_PREFIX)central-hub-network >/dev/null || docker network inspect $(HUB_NET_PREFIX)central-hub-network >/dev/null 2>&1 || { echo "❌ Could not create Docker network $(HUB_NET_PREFIX)central-hub-network — see the daemon error above."; exit 1; }; }

create-networks: create-networks-centralhub
	$(MAKE) -C trust create-networks

remove-networks:
	@echo "🗑️  Removing all networks..."
	@docker network rm $(HUB_NET_PREFIX)central-hub-network 2>/dev/null || true
	$(MAKE) -C trust remove-networks
	@echo "✅ All networks removed!"

recreate-networks: remove-networks create-networks
	@echo "🔄 All networks recreated for swarm deployment!"
	@echo "ℹ️  Trust networks now use overlay driver for swarm compatibility"

# Add a parameterized debug command
debug:
	@if [ -z "$(SERVICE)" ]; then \
		echo "❌ Usage: make debug SERVICE=<service-name>"; \
		echo "   Available services: data-access-api, imaging-api, trust-api, flip-api, fl-api-net-1"; \
		exit 1; \
	fi
	@echo "🚨 Starting debug mode for $(SERVICE)..."
	@case "$(SERVICE)" in \
		data-access-api|imaging-api|trust-api) \
			DEBUG=true $(MAKE) -C trust debug-$(SERVICE) ;; \
		flip-api|fl-api-net-1) \
			DEBUG=true $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d $(SERVICE) ;; \
		*) \
			echo "❌ Unknown service: $(SERVICE)"; exit 1 ;; \
	esac

debug-off:
	@if [ -z "$(SERVICE)" ]; then \
		echo "❌ Usage: make debug-off SERVICE=<service-name>"; \
		exit 1; \
	fi
	@echo "🚨 Stopping debug mode for $(SERVICE)..."
	@case "$(SERVICE)" in \
		data-access-api|imaging-api|trust-api) \
			DEBUG=false $(MAKE) -C trust debug-$(SERVICE)-off ;; \
		flip-api) \
			DEBUG=false $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d $(SERVICE) ;; \
		fl-api) \
			DEBUG=false $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d $(SERVICE) ;; \
		*) \
			echo "❌ Unknown service: $(SERVICE)"; exit 1 ;; \
	esac
	@echo "✅ Debug mode for $(SERVICE) stopped successfully!"

.PHONY: print-docker-tag
print-docker-tag:  ## Print the current DOCKER_TAG value
	@echo "DOCKER_TAG=$(DOCKER_TAG)"

up-pgadmin:
	${DOCKER_COMMAND} up -d pgadmin

unit_test:
	$(MAKE) -C flip-api unit_test
	$(MAKE) -C flip-ui unit_test
	$(MAKE) -C trust/data-access-api unit_test
	$(MAKE) -C trust/imaging-api unit_test
	$(MAKE) -C trust/omop-db unit_test
	$(MAKE) -C trust/trust-api unit_test
	$(MAKE) -C trust/xnat unit_test

integration_test:
	$(MAKE) -C flip-api integration_test
	$(MAKE) -C trust integration_test

# Python projects managed by uv; each has its own pyproject.toml + uv.lock.
UV_PROJECTS := . flip-api docs trust/trust-api trust/imaging-api trust/data-access-api trust/omop-db trust/xnat/tests deploy/providers/AWS

# Regenerate every uv.lock so it matches its pyproject.toml. Run after changing
# dependencies in any service, or to refresh all lockfiles in one pass.
# Note: `uv lock` re-resolves all dependencies under each project's
# `exclude-newer` window, so transitive pin versions may shift even when no
# direct dependency changed.
lock:
	@for dir in $(UV_PROJECTS); do \
		echo "Locking $$dir"; \
		( cd $$dir && uv lock ) || exit 1; \
	done
	@echo "All uv.lock files regenerated."

# Drives a fresh project end-to-end against a running `make up` stack:
# create → approve → upload model → wait for image pull → start training.
# Defaults pick the chest-xray tutorial that matches FL_BACKEND (flower or
# nvflare). The defaults target the in-tree tutorials under
# fl-tutorials/<backend>/ — override via MODEL_FILES_DIR= / QUERY_FILE=.
# Useful for sanity-checking PRs without manually clicking through the UI.
# See flip-api/Makefile for overrides (MODEL_FILES_DIR, QUERY_FILE, EXTRA_ARGS).
e2e_smoke:
	$(MAKE) -C flip-api e2e_smoke $(if $(FL_BACKEND),FL_BACKEND=$(FL_BACKEND)) $(if $(MODEL_FILES_DIR),MODEL_FILES_DIR="$(abspath $(MODEL_FILES_DIR))") $(if $(QUERY_FILE),QUERY_FILE="$(abspath $(QUERY_FILE))") $(if $(EXTRA_ARGS),EXTRA_ARGS="$(EXTRA_ARGS)")

# Record the end-to-end demo video against the running dev stack: six
# Dockerised Cypress segments over the live UI (real Cognito, trusts, S3,
# FL training) with the slow waits handled off-camera between segments, then
# ffmpeg-assembled into one mp4. Local dev tool — not run in CI. Options via
# DEMO_ARGS (see flip-api/tests/demo_video.py), e.g. DEMO_ARGS="--skip-xnat".
demo-video:
	$(MAKE) -C flip-api demo_video $(if $(DEMO_ARGS),DEMO_ARGS="$(DEMO_ARGS)")

# Provision the demo Cognito users the recorder signs in as (passwords from
# DEMO_RESEARCHER_PASSWORD / DEMO_ADMIN_PASSWORD env vars, never committed).
demo-users:
	$(MAKE) -C flip-api create_demo_users

# Pre-populate the platform with a curated catalogue of radiology projects in
# honest lifecycle states (no fabricated metrics/results). Cleanup:
# make seed-demo-projects EXTRA_ARGS="--cleanup"
seed-demo-projects:
	$(MAKE) -C flip-api seed_demo_projects $(if $(EXTRA_ARGS),EXTRA_ARGS="$(EXTRA_ARGS)")

generate-internal-service-key:
	$(MAKE) -C flip-api generate-internal-service-key $(if $(ENV_FILE),ENV_FILE=$(ENV_FILE)) $(if $(FORCE),FORCE=$(FORCE))

# Poll flip-api /api/health before registering. The entrypoint seeds the DB —
# including the FL kit-slot pool that register_trust claims from — *before* the
# API answers, so a healthy /api/health is a safe "seed complete" signal.
_wait-for-hub:
	@echo "⏳ Waiting for the hub (flip-api) to be ready on :$(API_PORT)..."
	@for i in $$(seq 1 90); do \
	  if curl -sf "http://localhost:$(API_PORT)/api/health" >/dev/null 2>&1; then \
	    echo "✅ Hub ready."; break; \
	  fi; \
	  if [ $$i -eq 90 ]; then echo "❌ flip-api not ready after 180s — is the hub up?"; exit 1; fi; \
	  sleep 2; \
	done

# Register one trust on the running hub from its kit file. KIT is the trust
# CODE (the kit-file handle). Seeds trust/.env.<CODE>.<env> from its .example on
# first run, reads TRUST_NAME/CODE/REGION from it, registers (idempotent — an
# already-registered name is skipped, credentials preserved), then writes the
# minted credentials + assigned FL kit slot + hub-shared block back into the
# kit. The hub keeps no trust list — the kit files ARE the list.
register-trust: _wait-for-hub
	@[ -n "$(KIT)" ] || { echo "❌ KIT=<CODE> required (e.g. KIT=GSTT)"; exit 1; }
	@kit="trust/.env.$(KIT).$(ENV)"; ex="$$kit.example"; \
	  if [ ! -f "$$kit" ]; then \
	    [ -f "$$ex" ] || { echo "❌ Neither $$kit nor $$ex exists — run 'make new-trust TRUST_CODE=$(KIT) TRUST_NAME=...' first."; exit 1; }; \
	    cp "$$ex" "$$kit"; chmod 600 "$$kit"; echo "📋 Seeded $$kit from $$(basename "$$ex")"; \
	  fi; \
	  name="$$(sed -n 's/^TRUST_NAME=//p' "$$kit" | head -1)"; \
	  code="$$(sed -n 's/^TRUST_CODE=//p' "$$kit" | head -1)"; \
	  region="$$(sed -n 's/^TRUST_REGION=//p' "$$kit" | head -1)"; \
	  [ -n "$$name" ] || { echo "❌ TRUST_NAME not set in $$kit"; exit 1; }; \
	  [ -n "$$code" ] || { echo "❌ TRUST_CODE not set in $$kit — code is required to register a trust"; exit 1; }; \
	  echo "🔑 Registering trust '$$name' (KIT=$(KIT))..."; \
	  set -- --name "$$name" --code "$$code"; \
	  [ -n "$$region" ] && set -- "$$@" --region "$$region"; \
	  kitjson="$$($(DOCKER_COMMAND) exec -T flip-api uv run python -m flip_api.scripts.register_trust "$$@")" \
	    || { echo "❌ register_trust failed for KIT=$(KIT)"; exit 1; }; \
	  printf '%s\n' "$$kitjson" | uv run --no-config scripts/distribute_trust_kits.py --target "$$kit"
	@$(MAKE) generate-xnat-credentials KIT=$(KIT)

# Register every dev trust: the shipped trust/.env.<CODE>.<env>.example kits ARE
# the roster (each is seeded to a live kit + registered). Used by `make up`.
register-trusts:
	@rc=0; found=0; for ex in trust/.env.*.$(ENV).example; do \
	  [ -e "$$ex" ] || continue; \
	  code="$${ex#trust/.env.}"; code="$${code%.$(ENV).example}"; \
	  found=1; $(MAKE) register-trust KIT="$$code" || rc=1; \
	done; \
	[ "$$found" = 1 ] || echo "ℹ️  No trust/.env.*.$(ENV).example kits found — nothing to register."; \
	[ "$$rc" = 0 ] || echo "❌ One or more trust registrations failed."; \
	exit $$rc

# Scaffold a new trust kit (trust/.env.<CODE>.<env>) from the base template.
# e.g. make new-trust TRUST_CODE=KCL TRUST_NAME="Kings College" PROD=true
new-trust:
	@[ -n "$(TRUST_CODE)" ] || { echo "❌ TRUST_CODE=<code> required (e.g. TRUST_CODE=KCL)"; exit 1; }
	@[ -n "$(TRUST_NAME)" ] || { echo "❌ TRUST_NAME=<name> required (e.g. TRUST_NAME='Kings College')"; exit 1; }
	@uv run --no-config scripts/new_trust.py --code "$(TRUST_CODE)" --name "$(TRUST_NAME)" \
		$(if $(TRUST_REGION),--region "$(TRUST_REGION)") --env $(ENV)

# Refresh the Hub-shared block in trust/.env.<CODE>.<env> with current
# $(MAIN_ENV_FILE) values. Preserves credentials. Safe to run repeatedly. Use
# after rotating AES_KEY_BASE64 or bumping image tags on the hub side — does NOT
# redistribute the updated kit file to remote operators (still out-of-band).
# On prod this is REQUIRED after `register-trust`: register fills only the
# hub-shared vars present in the flip-api task env (AES_KEY_BASE64,
# TRUST_API_KEY_HEADER, FL_BACKEND); the rest come from the admin's local env here.
#
# Works identically for dev/stag/prod: the Hub-shared values live in
# $(MAIN_ENV_FILE), which the root Makefile already `include`s + exports.
# No docker compose exec, no ECS round-trip, no per-trust TRUST_<n>_NAME
# lookup — just a file→file copy keyed on KIT.
sync-trust-kit:
	@[ -n "$(KIT)" ] || (echo "❌ KIT=<CODE> required (e.g. KIT=GSTT)"; exit 1)
	@MAIN_ENV_FILE=$(MAIN_ENV_FILE) uv run --no-config scripts/sync_trust_kit.py $(KIT).$(ENV)

# Sync every locally-present kit for this env. Globs trust/.env.*.$(ENV) and
# drops the .example templates (which don't match the bare-env suffix).
sync-trust-kits:
	@found=0; \
	for f in trust/.env.*.$(ENV); do \
	    case "$$f" in *.example) continue;; esac; \
	    [ -f "$$f" ] || continue; \
	    code="$${f#trust/.env.}"; code="$${code%.$(ENV)}"; \
	    found=$$((found + 1)); \
	    $(MAKE) sync-trust-kit KIT="$$code"; \
	done; \
	if [ "$$found" = "0" ]; then \
	    echo "ℹ️  No trust/.env.*.$(ENV) kits found — nothing to sync."; \
	fi

# ---------------------------------------------------------------------------
# Kubernetes Helm chart targets
# ---------------------------------------------------------------------------
deploy-trust-k8s: ## Deploy trust services to Kubernetes via Helm
	$(MAKE) -C deploy/providers/kubernetes deploy

undeploy-trust-k8s: ## Remove trust services from Kubernetes
	$(MAKE) -C deploy/providers/kubernetes undeploy

# Mint the XNAT stack passwords (XNAT_DATASOURCE_PASSWORD,
# XNAT_DATASOURCE_ADMIN_PASSWORD, XNAT_ACTIVEMQ_PASSWORD) into kit files —
# they are runtime-only secrets, never committed and never baked into the
# published XNAT image (FLIP-PT-056). Runs automatically inside
# `register-trust`; invoke directly to backfill every local kit, one kit
# (KIT=<CODE>), an explicit file (ENV_FILE=<path>), or rotate (FORCE=1).
# Existing non-placeholder values are preserved unless FORCE=1.
generate-xnat-credentials:
	@if [ -n "$(ENV_FILE)" ]; then \
	  $(MAKE) -C flip-api generate-xnat-credentials ENV_FILE=$(ENV_FILE) $(if $(FORCE),FORCE=$(FORCE)); \
	elif [ -n "$(KIT)" ]; then \
	  $(MAKE) -C flip-api generate-xnat-credentials ENV_FILE=$(CURDIR)/trust/.env.$(KIT).$(ENV) $(if $(FORCE),FORCE=$(FORCE)); \
	else \
	  found=0; for kit in trust/.env.*.$(ENV); do \
	    [ -e "$$kit" ] || continue; case "$$kit" in *.example) continue;; esac; \
	    found=1; $(MAKE) -C flip-api generate-xnat-credentials ENV_FILE=$(CURDIR)/$$kit $(if $(FORCE),FORCE=$(FORCE)) || exit 1; \
	  done; \
	  [ "$$found" = 1 ] || echo "ℹ️  No trust/.env.<CODE>.$(ENV) kit files found — run 'make register-trusts' (or 'make register-trust KIT=<CODE>') first."; \
	fi

check-aws-access:
	@echo "🔎 Checking AWS CLI access..."
	@if ! command -v aws >/dev/null 2>&1; then \
		echo "❌ ERROR: AWS CLI is not installed or not in PATH."; \
		exit 1; \
	fi
	@if ! aws sts get-caller-identity >/dev/null 2>&1; then \
		echo "❌ ERROR: AWS is not accessible. Check credentials, profile, and network access."; \
		exit 1; \
	fi
	@echo "✅ AWS access confirmed."
