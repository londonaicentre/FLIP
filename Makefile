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

.PHONY: build dev prod clean stop up down up-no-trust up-trusts central-fl central-hub \
		restart restart-fl restart-no-trust ci tests debug create-networks remove-networks recreate-networks consolidate-deps \
		check-aws-access up-local-trust generate-internal-service-key \
		register-trust-1 register-trust-2 register-trusts _wait-for-hub integration_test \
		sync-trust-kit-1 sync-trust-kit-2 sync-trust-kits

ifeq ($(PROD),true)
MAIN_ENV_FILE=.env.production
__DCKR_SUFFIX=production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=.env.stag
__DCKR_SUFFIX=production
else
MAIN_ENV_FILE=.env.development
__DCKR_SUFFIX=development
endif

# Print which environment files are being used
$(info Using MAIN_ENV_FILE: $(MAIN_ENV_FILE))

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

include deploy/fl_backend.mk

COMMON_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Resolve FL_PROVISIONED_DIR (from .env) to an absolute path relative to this Makefile
# Docker requires absolute paths for volume mounts; the .env value may be relative
MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
override FL_PROVISIONED_DIR := $(abspath $(MAKEFILE_DIR)/$(FL_PROVISIONED_DIR))

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
DOCKER_COMMAND=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f deploy/compose.development.debug.override.yml
SHOW_LOGS_CENTRAL_HUB=docker logs -f flip-api --tail 100 --timestamps --follow
GENERIC_LOGS=docker logs -f --tail 100 --timestamps --follow

# NOTE DOCKER_FL_REGISTRY is set to empty when we use local FL images during development (e.g. flare-fl-server:dev).
# In that case, '--pull always' will error because docker won't be able to find the manifest for the dev image online,
# so we need to remove the --pull always flag. Non-FL images keep DOCKER_REGISTRY (always GHCR), so they're not the
# constraint here — the check fires only when the FL registry is empty.
ifneq ($(strip $(DOCKER_FL_REGISTRY)),)
PULL_ALWAYS_FLAG=--pull always
else
PULL_ALWAYS_FLAG=
endif

# Build the Docker images
build:
	@echo "🛠️ Building Docker images..."
	@echo "UI_PORT = $(UI_PORT)"
	${DOCKER_COMMAND} build --no-cache
	$(MAKE) -C trust build
	$(MAKE) -C trust/xnat build
	@echo "✅ Docker images built successfully!"

# Run all services
# Uses --pull always to ensure the latest FL images are used
up: check-aws-access generate-internal-service-key create-networks
	@echo "🚢 Starting all services..."
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d $(PULL_ALWAYS_FLAG)
	@echo "🔑 Registering trusts and writing kit files..."
	$(MAKE) register-trusts
	@echo "🚢 Starting trust services (each trust brings up its own XNAT)..."
	$(MAKE) -C trust up
	@echo "✅ All services started successfully!"

# Minimal $(MAKE) up
up-no-trust: generate-internal-service-key create-networks
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d $(PULL_ALWAYS_FLAG)

up-trusts: create-networks
	@echo "🔑 Registering trusts and writing kit files (hub must already be up)..."
	$(MAKE) register-trusts
	@echo "🚢 Starting Trust services (each trust brings up its own XNAT)..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust up
	@echo "✅ Trust services started successfully!"

# Uses --pull always to ensure the latest FL images and 'stag'/'prod' version are used
up-centralhub-ec2: create-networks-centralhub
	@echo "Hey! PROD="$(PROD)
	@echo "Hey! UI_PORT="$(UI_PORT)
	@echo "🚢 Starting central hub API services..."
	PROVIDER=AWS \
	DOCKER_TAG=$(DOCKER_TAG) \
	DOCKER_FL_TAG=$(DOCKER_FL_TAG) \
	${DOCKER_COMMAND} up --remove-orphans -d --pull always
	@echo "✅ Central hub API services started successfully!"

up-trust-ec2: create-networks
	@echo "Hey! PROD="$(PROD)
	@echo "Hey! UI_PORT="$(UI_PORT)
	@echo "🚢 Starting Trust services..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust up-trust-ec2 KIT=Trust_1 PROD=${PROD}
	@echo "✅ Trust services started successfully!"

LOCAL_TRUST_NAME ?= Trust_Local

up-local-trust: create-networks
	docker context use default
	@echo "🚢 Starting local on-prem Trust services (PROD=$(PROD), TRUST_NAME=$(LOCAL_TRUST_NAME))..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust up-local-trust PROD=$(PROD) LOCAL_TRUST_NAME=$(LOCAL_TRUST_NAME)
	@echo "✅ Local Trust services started successfully!"

central-hub: create-networks-centralhub
	$(MAKE) -C flip-api up

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
# NOTE: Uses $(PULL_ALWAYS_FLAG) — pulls fresh FL images only when DOCKER_FL_REGISTRY is set
#       (same logic as `up`); an empty registry keeps locally built flip-fl-base-flower images.
# NOTE: Client keys must be re-registered before starting clients (Flower only)
restart-fl:
	@echo "🔄 Restarting FL services ($(FL_BACKEND))..."
	@echo "🔄 Step 1: Stopping and removing old FL clients..."
	$(MAKE) -C trust down-fl-clients
	@echo "🔄 Step 2: Restarting FL APIs and servers..."
	${DOCKER_COMMAND} up -d --force-recreate --no-deps $(PULL_ALWAYS_FLAG) fl-api-net-1 fl-api-net-2 fl-server-net-1 fl-server-net-2
	@if [ "$(FL_BACKEND)" = "flower" ]; then \
		echo "🔄 Step 3: Registering new client keys with FL servers (Flower only)..."; \
		${DOCKER_COMMAND} up --force-recreate $(PULL_ALWAYS_FLAG) register-supernode-keys-net-1 register-supernode-keys-net-2; \
	fi
	@echo "🔄 Step 4: Starting new FL clients..."
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
	@{ docker network inspect central-hub-network >/dev/null 2>&1 || docker network create --driver bridge central-hub-network || true; }

create-networks: create-networks-centralhub
	$(MAKE) -C trust create-networks

remove-networks:
	@echo "🗑️  Removing all networks..."
	@docker network rm central-hub-network 2>/dev/null || true
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
	$(MAKE) -C trust/trust-api unit_test
	$(MAKE) -C trust/xnat unit_test

integration_test:
	$(MAKE) -C flip-api integration_test
	$(MAKE) -C trust integration_test

# Drives a fresh project end-to-end against a running `make up` stack:
# create → approve → upload model → wait for image pull → start training.
# Defaults pick the chest-xray tutorial that matches FL_BACKEND (flower or
# nvflare); both sit in sibling repos (flip-fl-base / flip-fl-base-flower).
# Useful for sanity-checking PRs without manually clicking through the UI.
# See flip-api/Makefile for overrides (MODEL_FILES_DIR, QUERY_FILE, EXTRA_ARGS).
e2e_smoke:
	$(MAKE) -C flip-api e2e_smoke $(if $(FL_BACKEND),FL_BACKEND=$(FL_BACKEND)) $(if $(MODEL_FILES_DIR),MODEL_FILES_DIR=$(MODEL_FILES_DIR)) $(if $(QUERY_FILE),QUERY_FILE=$(QUERY_FILE)) $(if $(EXTRA_ARGS),EXTRA_ARGS="$(EXTRA_ARGS)")

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

# Register one trust on the running hub (idempotent — a trust whose name already
# exists is skipped, its kit file left untouched) and write/refresh its kit file
# under trust/.env.<slot>. TRUST_<n>_* come from the env file; the hub is never
# told the list. register-trusts runs both in order so trust 1 claims the first
# free FL kit slot, trust 2 the next.
register-trust-1: _wait-for-hub
	@echo "🔑 Registering trust 1 ($(TRUST_1_NAME))..."
	@$(DOCKER_COMMAND) exec -T flip-api uv run python -m flip_api.scripts.register_trust \
		--name "$(TRUST_1_NAME)" $(if $(TRUST_1_CODE),--code "$(TRUST_1_CODE)") $(if $(TRUST_1_REGION),--region "$(TRUST_1_REGION)") \
		| bash scripts/distribute-trust-kits.sh

register-trust-2: _wait-for-hub
	@echo "🔑 Registering trust 2 ($(TRUST_2_NAME))..."
	@$(DOCKER_COMMAND) exec -T flip-api uv run python -m flip_api.scripts.register_trust \
		--name "$(TRUST_2_NAME)" $(if $(TRUST_2_CODE),--code "$(TRUST_2_CODE)") $(if $(TRUST_2_REGION),--region "$(TRUST_2_REGION)") \
		| bash scripts/distribute-trust-kits.sh

register-trusts: register-trust-1 register-trust-2

# Refresh the Hub-shared block in an existing kit file with current MAIN_ENV_FILE
# values. Preserves credentials. Safe to run repeatedly. Use after rotating
# AES_KEY_BASE64 or bumping image tags on the hub side — does NOT redistribute
# the updated kit file to remote operators (that step is still out-of-band).
sync-trust-kit-1: _wait-for-hub
	@echo "🔄 Syncing hub-shared block for trust 1 ($(TRUST_1_NAME))..."
	@$(DOCKER_COMMAND) exec -T flip-api uv run python -m flip_api.scripts.register_trust \
		--name "$(TRUST_1_NAME)" $(if $(TRUST_1_CODE),--code "$(TRUST_1_CODE)") $(if $(TRUST_1_REGION),--region "$(TRUST_1_REGION)") \
		--require-existing \
		| jq -c 'map(del(.trust_api_key, .trust_internal_service_key))' \
		| bash scripts/sync-trust-kits.sh

sync-trust-kit-2: _wait-for-hub
	@echo "🔄 Syncing hub-shared block for trust 2 ($(TRUST_2_NAME))..."
	@$(DOCKER_COMMAND) exec -T flip-api uv run python -m flip_api.scripts.register_trust \
		--name "$(TRUST_2_NAME)" $(if $(TRUST_2_CODE),--code "$(TRUST_2_CODE)") $(if $(TRUST_2_REGION),--region "$(TRUST_2_REGION)") \
		--require-existing \
		| jq -c 'map(del(.trust_api_key, .trust_internal_service_key))' \
		| bash scripts/sync-trust-kits.sh

sync-trust-kits: sync-trust-kit-1 sync-trust-kit-2

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
