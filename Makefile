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
		restart restart-no-trust ci tests debug create-networks remove-networks recreate-networks consolidate-deps

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
# Export all variables except DOCKER_TAG (which we'll set dynamically below)
# export $(shell sed 's/=.*//' $(MAIN_ENV_FILE) | grep -v '^DOCKER_TAG$$')
# NOTE The above no longer works as CICD tags are not based on PR numbers anymore since github actions are run manually.
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

# Override DOCKER_TAG with PR number if available (uses GitHub CLI)
# Falls back to "stag" if no PR is found or gh CLI is not available
# Using 'override' to ensure this takes precedence over the value from .env files
ifeq ($(PROD),true)
override DOCKER_TAG := prod
else ifeq ($(PROD),stag)
override DOCKER_TAG := stag
else
override DOCKER_TAG := $(shell gh pr view --json number -q '"pr-" + (.number | tostring)' 2>/dev/null || echo "stag")
endif
export DOCKER_TAG
# NOTE The above no longer works as CICD tags are not based on PR numbers anymore since github actions are run manually.
# override DOCKER_TAG := $(shell gh pr view --json number -q '"pr-" + (.number | tostring)' 2>/dev/null || echo "stag")
# export DOCKER_TAG

# ---- FL backend selection (flower | nvflare) ----
FL_BACKEND ?= flower
VALID_FL_BACKENDS := flower nvflare

ifeq (,$(filter $(FL_BACKEND),$(VALID_FL_BACKENDS)))
$(error Invalid FL_BACKEND '$(FL_BACKEND)'. Must be one of: $(VALID_FL_BACKENDS))
endif

COMMON_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := deploy/compose.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Override FL_PROVISIONED_DIR to use absolute path resolved relative to this Makefile
# This allows the repo to work on any machine without hardcoding paths
override FL_PROVISIONED_DIR := $(shell realpath $(dir $(lastword $(MAKEFILE_LIST)))/../flip-fl-base/workspace)
export FL_PROVISIONED_DIR

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

# Build the Docker images
build:
	@echo "🛠️ Building Docker images..."
	@echo "UI_PORT = $(UI_PORT)"
	${DOCKER_COMMAND} build
	$(MAKE) -C trust build
	$(MAKE) -C trust/xnat build
	@echo "✅ Docker images built successfully!"

# Run all services
# Uses --pull always to ensure the latest FL images are used
up: create-networks
	@echo "🚢 Starting all services..."
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d --pull always
	@echo "🚢 Starting trust services..."
	$(MAKE) -C trust up
	@echo "🚢 Starting XNAT services..."
	$(MAKE) -C trust/xnat up-swarm
	@echo "✅ All services started successfully!"

# Minimal $(MAKE) up
up-no-trust: create-networks
	@echo "🚢 Starting central hub API services..."
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	${DOCKER_COMMAND} up --remove-orphans -d --pull always

up-trusts: create-networks
	@echo "🚢 Starting Trust services..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust up
	@echo "🚢 Starting XNAT services..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust/xnat up-swarm
	@echo "✅ Trust services started successfully!"

# Uses --pull always to ensure the latest FL images and 'stag' version are used
up-centralhub-stag: create-networks
	@echo "Hey! PROD="$(PROD)
	@echo "Hey! UI_PORT="$(UI_PORT)
	@echo "🚢 Starting central hub API services..."
	PROVIDER=AWS \
	DOCKER_TAG=$(DOCKER_TAG) \
	DOCKER_FL_TAG=$(DOCKER_FL_TAG) \
	${DOCKER_COMMAND} up --remove-orphans -d --pull always
	@echo "✅ Central hub API services started successfully!"

up-trust-stag: create-networks
	@echo "Hey! PROD="$(PROD)
	@echo "Hey! UI_PORT="$(UI_PORT)
	@echo "🚢 Starting Trust services..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust up-trust-1-stag PROD=stag
	@echo "🚢 Starting XNAT services..."
	$(MAKE) -e DEBUG=$(DEBUG) -C trust/xnat up-xnat-1-stag PROD=stag
	@echo "✅ Trust services started successfully!"

central-hub: create-networks
	$(MAKE) -C flip-api up

# Stop all containers
down:
	@echo "🛑 Stopping all services..."
	$(MAKE) -C trust/xnat down-swarm
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

# Stop and start all services except the trust services related services
restart-no-trust:
	@echo "Debug mode: '${DEBUG}'"
	@echo "Passing DEBUG=${DEBUG} to the downstream $(MAKE) commands..."
	$(MAKE) -e DEBUG=$(DEBUG) -C flip-api restart
ci:
	act --env-file .env.development
ui:
	@echo "🚀 Starting UI..."
	$(DOCKER_COMMAND) up --remove-orphans -d flip-ui
ui-off:
	@echo "🛑 Stopping UI..."
	$(DOCKER_COMMAND) down --remove-orphans flip-ui
tests:
	cd flip-ui && $(MAKE) tests && \
	cd ../flip-api && $(MAKE) test

debug-all:
	@echo "🚨 Starting debug mode by overriding the DEBUG environment variable..."
	DEBUG=true $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d
	$(MAKE) -C trust debug
debug-off-all:
	@echo "🚨 Stopping debug mode by removing the DEBUG environment variable override..."
	$(MAKE) -C flip-api delete_testing_projects
	DEBUG=false $(DEBUG_OVERRIDE_COMPOSE_COMMAND) up --remove-orphans -d
	$(MAKE) -C trust debug-off

create-networks:
	@{ docker network inspect central-hub-network >/dev/null 2>&1 || docker network create --driver bridge central-hub-network || true; }
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
