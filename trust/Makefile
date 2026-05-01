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

.PHONY: build dev prod clean stop up down up-trust-1 up-trust-2 down-trust-1 down-trust-2 up-local-trust down-local-trust create-networks create-networks-local-trust remove-networks recreate-networks tests update-omop-data update-orthanc-data
# if tag PROD=true is passed, set PROD=true
ifeq ($(PROD),true)
MAIN_ENV_FILE=../.env.production
__DCKR_SUFFIX=production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=../.env.stag
__DCKR_SUFFIX=production
else
MAIN_ENV_FILE=../.env.development
__DCKR_SUFFIX=development
endif

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

include ../deploy/fl_backend.mk

COMMON_COMPOSE_FILE := compose_trust.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := compose_trust.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Resolve FL_PROVISIONED_DIR (from .env) to an absolute path relative to the repo root.
# Docker requires absolute paths for volume mounts; the .env value may be relative.
# Skip if already absolute (the parent Makefile resolves it before invoking sub-make).
MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ifeq ($(patsubst /%,,$(FL_PROVISIONED_DIR)),$(FL_PROVISIONED_DIR))
override FL_PROVISIONED_DIR := $(abspath $(MAKEFILE_DIR)/../$(FL_PROVISIONED_DIR))
endif

network1 = deploy_trust-network-1
network2 = deploy_trust-network-2
network_local = deploy_trust-network-local
trust1 = trust1
trust2 = trust2
trust_local = trust-local

get_json_value = $(shell python3 ../flip-api/src/flip_api/scripts/env_utils.py '$(1)' $(2))

OMOP_DB_CMD=make -C ./omop-db
ORTHANC_CMD=make -C ./orthanc
DOCKER_COMPOSE_CMD=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f compose_trust.debug.override.yml

# NOTE DOCKER_REGISTRY is set to empty when we use local FL images during development (e.g. flare-fl-server:dev). 
# In that case, '--pull always' will error because docker won't be able to find the manifest for the dev image online,
# so we need to remove the --pull always flag.
ifneq ($(strip $(DOCKER_REGISTRY)),)
PULL_ALWAYS_FLAG=--pull always
else
PULL_ALWAYS_FLAG=
endif

TRUST_1_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST1} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_1} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_1} \
	ORTHANC_STORAGE_DIR=${ORTHANC_STORAGE_DIR_TRUST_1} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_1} \
	DATA_ACCESS_API_PORT=${DATA_ACCESS_API_PORT} \
	TRUST_DEBUG_PORT=${TRUST_DEBUG_PORT_TRUST_1} \
	IMAGING_DEBUG_PORT=${IMAGING_DEBUG_PORT_TRUST_1} \
	DATA_ACCESS_DEBUG_PORT=${DATA_ACCESS_DEBUG_PORT_TRUST_1} \
	GRAFANA_PORT=${GRAFANA_PORT_TRUST_1} \
	LOKI_PORT=${LOKI_PORT_TRUST_1} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=Trust_1 \
	TRUST_NUMBER=1 \
	TRUST_API_KEY=$(call get_json_value,$(TRUST_API_KEYS),Trust_1) \
	TRUST_INTERNAL_SERVICE_KEY=$(call get_json_value,$(TRUST_INTERNAL_SERVICE_KEYS),Trust_1)
TRUST_2_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST2} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_2} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_2} \
	ORTHANC_STORAGE_DIR=${ORTHANC_STORAGE_DIR_TRUST_2} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_2} \
	TRUST_DEBUG_PORT=${TRUST_DEBUG_PORT_TRUST_2} \
	IMAGING_DEBUG_PORT=${IMAGING_DEBUG_PORT_TRUST_2} \
	DATA_ACCESS_DEBUG_PORT=${DATA_ACCESS_DEBUG_PORT_TRUST_2} \
	GRAFANA_PORT=${GRAFANA_PORT_TRUST_2} \
	LOKI_PORT=${LOKI_PORT_TRUST_2} \
	TRUST_NETWORK_NAME=${network2} \
	TRUST_NAME=Trust_2 \
	TRUST_NUMBER=2 \
	TRUST_API_KEY=$(call get_json_value,$(TRUST_API_KEYS),Trust_2) \
	TRUST_INTERNAL_SERVICE_KEY=$(call get_json_value,$(TRUST_INTERNAL_SERVICE_KEYS),Trust_2)

# Host ports, bind paths, and the trust network are hardcoded here (not env
# vars) so the local-trust stack can coexist with dev trust1/trust2 on one
# laptop without colliding, and so stag/prod hosts (AWS trust EC2 or on-prem
# via `make add-local-trust`) don't need any _LOCAL_TRUST entries in their
# env files or AWS Secrets Manager. Port numbers / dir names are arbitrary —
# picked to be non-overlapping with the _TRUST_1/_TRUST_2 allocations.
#
# ORTHANC_STORAGE_DIR gets a dedicated `orthanc-storage-local` path rather
# than reusing whichever TRUST_$(N) dir matches LOCAL_TRUST_NAME. Sharing the
# dir saves the gigabytes of seeded DICOM data, but two Orthanc containers
# cannot open the same SQLite index — the second crash-loops on `database is
# locked` and the trust network loses its `orthanc` alias, which in turn
# breaks xnat-nginx upstream resolution. To re-seed the local-trust Orthanc
# on a dev laptop, copy from the matching trust dir once:
#   cp -r trust/orthanc/orthanc-storage-2/ trust/orthanc/orthanc-storage-local/
LOCAL_TRUST_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=./data/local/$(LOCAL_TRUST_NAME) \
	OMOP_DB_PORT=5438 \
	PACS_UI_PORT=8046 \
	XNAT_PORT=8108 \
	GRAFANA_PORT=3002 \
	LOKI_PORT=3102 \
	TRUST_NETWORK_NAME=${network_local} \
	TRUST_NAME=$(LOCAL_TRUST_NAME) \
	TRUST_NUMBER=$(subst Trust_,,$(LOCAL_TRUST_NAME)) \
	TRUST_API_KEY=$(call get_json_value,$(TRUST_API_KEYS),$(LOCAL_TRUST_NAME)) \
	TRUST_INTERNAL_SERVICE_KEY=$(call get_json_value,$(TRUST_INTERNAL_SERVICE_KEYS),$(LOCAL_TRUST_NAME)) \
	OMOP_DATA_DIR=./omop-db/volumes/$(LOCAL_TRUST_NAME)/db_data \
	ORTHANC_STORAGE_DIR=./orthanc/orthanc-storage-local \
	OBSERVABILITY_CONFIG_DIR=./observability \
	LOKI_DATA_VOLUME=trust-local-loki-data \
	GRAFANA_DATA_VOLUME=trust-local-grafana-data

build:
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust1} build --no-cache
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} build

# Uses --pull always to ensure the latest FL images and omop-db image are used
# ensure omop and orthanc data are up to date before bringing up trust 1
up-trust-1: update-omop-data update-orthanc-data
	@echo "🚢 Starting Trust 1 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	mkdir -p ${BASE_IMAGES_DOWNLOAD_DIR_TRUST1}
	$(TRUST_1_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -f compose_trust-1_override.yml -p ${trust1} up -d $(PULL_ALWAYS_FLAG)

down-trust-1:
	@echo "🛑 Stopping Trust 1 services..."
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -f compose_trust-1_override.yml -p ${trust1} down --remove-orphans

restart-trust-1: down-trust-1 up-trust-1

# Uses --pull always to ensure the latest FL images, 'stag' or 'prod' version, and omop-db images are used
up-trust-1-ec2:
# omop data will be downloaded onto the ec2 instance as part of deploy/providers/AWS/site.yml, so no need to do it here
# NOTE eventually if we get AWSCLI on the ec2 instance by default then we could run update-omop-data here too.
	@echo "🚢 Starting Trust 1 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(TRUST_1_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -p ${trust1} up -d --pull always

down-trust-1-ec2:
	@echo "🛑 Stopping Trust 1 services..."
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust1} down --remove-orphans

# LOCAL_TRUST_NAME controls which FL participant kit the FL client mounts.
# Local (on-prem) trusts default to the Trust_2 kit; EC2 trusts use Trust_1.
# LOCAL_TRUST_NAME is defined in deploy/providers/AWS/Makefile and threaded
# through the root Makefile.
#
# Both recipes below use $(LOCAL_TRUST_VARS) so they stay in sync — see the
# block near the top of this file for why each env-var has a _LOCAL_TRUST
# variant (dev-laptop coexistence with `make up`) and why unset values fall
# back to the Trust_1 defaults (single-host stag/prod deploys).
up-local-trust: create-networks-core create-networks-local-trust
	## Starts local trust compose stack with TRUST_NAME=$(LOCAL_TRUST_NAME).
	## Uses project name 'trust-local' to avoid collisions with dev trust1/trust2.
	@echo "🚢 Starting local Trust services (TRUST_NAME=$(LOCAL_TRUST_NAME))..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	mkdir -p ./data/local/$(LOCAL_TRUST_NAME)
	$(LOCAL_TRUST_VARS) DEBUG=$(DEBUG) \
		$(DOCKER_COMPOSE_CMD) -f compose_trust.local.yml -p $(trust_local) up -d --pull always

down-local-trust:
	@echo "🛑 Stopping local Trust services..."
	$(LOCAL_TRUST_VARS) \
		$(DOCKER_COMPOSE_CMD) -f compose_trust.local.yml -p $(trust_local) down --remove-orphans

# Uses --pull always to ensure the latest FL images and omop-db image are used
# ensure omop and orthanc data are up to date before bringing up trust 2
up-trust-2: update-omop-data update-orthanc-data
	@echo "🚢 Starting Trust 2 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	mkdir -p ${BASE_IMAGES_DOWNLOAD_DIR_TRUST2}
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} up -d $(PULL_ALWAYS_FLAG)

down-trust-2:
	@echo "🛑 Stopping Trust 2 services..."
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} down --remove-orphans

restart-trust-2: down-trust-2 up-trust-2

up: create-networks up-trust-1 up-trust-2

down: down-trust-1 down-trust-2

update-omop-data:
	$(OMOP_DB_CMD) update-omop-data

update-orthanc-data:
	$(ORTHANC_CMD) update-orthanc-data

tests:
	cd data-access-api && make test && \
	cd ../imaging-api && make test && \
	cd ../trust-api && make test

create-networks-core:
	@echo "🔧 Ensuring external Docker networks exist..."
	@{ docker network inspect central-hub-trust-apis-network >/dev/null 2>&1 || docker network create --driver bridge central-hub-trust-apis-network || true; }
	@{ docker network inspect deploy_shared-net-1 >/dev/null 2>&1 || docker network create --driver bridge deploy_shared-net-1 || true; }
	@{ docker network inspect deploy_shared-net-2 >/dev/null 2>&1 || docker network create --driver bridge deploy_shared-net-2 || true; }

create-networks-trust-1:
	@{ docker network inspect deploy_trust-network-1 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-1; }

create-networks-trust-2:
	@{ docker network inspect deploy_trust-network-2 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-2; }

# Dedicated network for the local trust stack so it doesn't attach to
# deploy_trust-network-1 when dev trust1 is also running. Not wired into the
# top-level `create-networks` target because `make up` (dev) doesn't need it;
# `up-local-trust` requires it explicitly.
create-networks-local-trust:
	@{ docker network inspect $(network_local) >/dev/null 2>&1 || docker network create --driver overlay --attachable $(network_local); }

create-networks: create-networks-core create-networks-trust-1 create-networks-trust-2

remove-networks:
	@echo "🗑️  Removing all trust networks..."
	@docker network rm central-hub-trust-apis-network 2>/dev/null || true
	@docker network rm deploy_trust-network-1 2>/dev/null || true
	@docker network rm deploy_trust-network-2 2>/dev/null || true
	@docker network rm $(network_local) 2>/dev/null || true
	@docker network rm deploy_shared-net-1 2>/dev/null || true
	@docker network rm deploy_shared-net-2 2>/dev/null || true
	@echo "✅ Trust networks removed!"

recreate-networks: remove-networks create-networks
	@echo "✅ All trust networks recreated with swarm support!"

debug:
	$(MAKE) DEBUG=true restart-trust-1
debug-off:
	$(MAKE) DEBUG=false restart-trust-1
debug-data-access-api:
	DEBUG=true $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d data-access-api
debug-imaging-api:
	DEBUG=true $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d imaging-api
debug-trust-api:
	DEBUG=true $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d trust-api
debug-data-access-api-off:
	DEBUG=false $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d data-access-api
debug-imaging-api-off:
	DEBUG=false $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d imaging-api
debug-trust-api-off:
	DEBUG=false $(TRUST_1_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p ${trust1} up --remove-orphans -d trust-api

