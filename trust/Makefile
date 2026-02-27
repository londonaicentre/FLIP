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

.PHONY: build dev prod clean stop up down up-trust-1 up-trust-2 down-trust-1 down-trust-2 create-networks remove-networks recreate-networks tests generate-trust-certs verify-trust-certs test-trust-https
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

# ---- FL backend selection (flower | nvflare) ----
FL_BACKEND ?= flower
VALID_FL_BACKENDS := flower nvflare

ifeq (,$(filter $(FL_BACKEND),$(VALID_FL_BACKENDS)))
$(error Invalid FL_BACKEND '$(FL_BACKEND)'. Must be one of: $(VALID_FL_BACKENDS))
endif

COMMON_COMPOSE_FILE := compose_trust.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := compose_trust.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Override FL_PROVISIONED_DIR to use absolute path resolved relative to the parent Makefile
# This allows the repo to work on any machine without hardcoding paths
override FL_PROVISIONED_DIR := $(shell realpath ../../flip-fl-base/workspace)
export FL_PROVISIONED_DIR

network1 = deploy_trust-network-1
network2 = deploy_trust-network-2
trust1 = trust1
trust2 = trust2

OMOP_DB_CMD=make -C ./omop-db
DOCKER_COMPOSE_CMD=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f compose_trust.debug.override.yml

TRUST_1_VARS = \
	TRUST_NUMBER=1 \
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST1} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_1} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_1} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_1} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_1} \
	DATA_ACCESS_API_PORT=${DATA_ACCESS_API_PORT} \
	TRUST_DEBUG_PORT=${TRUST_DEBUG_PORT_TRUST_1} \
	IMAGING_DEBUG_PORT=${IMAGING_DEBUG_PORT_TRUST_1} \
	DATA_ACCESS_DEBUG_PORT=${DATA_ACCESS_DEBUG_PORT_TRUST_1} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=trust1
TRUST_2_VARS = \
	TRUST_NUMBER=2 \
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST2} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_2} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_2} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_2} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_2} \
	TRUST_DEBUG_PORT=${TRUST_DEBUG_PORT_TRUST_2} \
	IMAGING_DEBUG_PORT=${IMAGING_DEBUG_PORT_TRUST_2} \
	DATA_ACCESS_DEBUG_PORT=${DATA_ACCESS_DEBUG_PORT_TRUST_2} \
	TRUST_NETWORK_NAME=${network2} \
	TRUST_NAME=trust2

build:
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} build
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} build

# Uses --pull always to ensure the latest FL images and omop-db image are used
# ensure omop data is up to date before bringing up trust 1
up-trust-1: update-omop-data
	@echo "🚢 Starting Trust 1 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	mkdir -p ${BASE_IMAGES_DOWNLOAD_DIR_TRUST1}
	$(TRUST_1_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -f compose_trust-1_override.yml -p ${trust1} up -d

down-trust-1:
	@echo "🛑 Stopping Trust 1 services..."
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -f compose_trust-1_override.yml -p ${trust1} down --remove-orphans

restart-trust-1: down-trust-1 up-trust-1

# Uses --pull always to ensure the latest FL images, 'stag' version, and omop-db images are used
up-trust-1-stag:
# omop data will be downloaded onto the ec2 instance as part of deploy/providers/AWS/site.yml, so no need to do it here
# NOTE eventually if we get AWSCLI on the ec2 instance by default then we could run update-omop-data here too.
	@echo "🚢 Starting Trust 1 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(TRUST_1_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -p ${trust1} up -d --pull always

down-trust-1-stag:
	@echo "🛑 Stopping Trust 1 services..."
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust1} down --remove-orphans

# Uses --pull always to ensure the latest FL images and omop-db image are used
# ensure omop data is up to date before bringing up trust 2
up-trust-2: update-omop-data
	@echo "🚢 Starting Trust 2 services..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	mkdir -p ${BASE_IMAGES_DOWNLOAD_DIR_TRUST2}
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} up -d

down-trust-2:
	@echo "🛑 Stopping Trust 2 services..."
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} down --remove-orphans

restart-trust-2: down-trust-2 up-trust-2

up: create-networks up-trust-1 up-trust-2

down: down-trust-1 down-trust-2

update-omop-data:
	$(OMOP_DB_CMD) update-omop-data

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
	@{ docker network inspect deploy_trust-network-1 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-1 || true; }

create-networks-trust-2:
	@{ docker network inspect deploy_trust-network-2 >/dev/null 2>&1 || docker network create --driver overlay --attachable deploy_trust-network-2 || true; }

create-networks: create-networks-core create-networks-trust-1 create-networks-trust-2

remove-networks:
	@echo "🗑️  Removing all trust networks..."
	@docker network rm central-hub-trust-apis-network 2>/dev/null || true
	@docker network rm deploy_trust-network-1 2>/dev/null || true
	@docker network rm deploy_trust-network-2 2>/dev/null || true
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

generate-trust-certs:
	TRUST_HOST="$(TRUST_HOST)" bash certs/generate-trust-certs.sh

verify-trust-certs:
	@echo "--- Verifying cert chain ---"
	openssl verify -CAfile certs/trust-ca.crt certs/trust-server.crt
	@echo "--- Server cert SANs ---"
	openssl x509 -in certs/trust-server.crt -noout -ext subjectAltName
	@echo "--- Expiry ---"
	openssl x509 -in certs/trust-server.crt -noout -dates

test-trust-https:
	@[ -n "$(TRUST_HOST)" ] || (echo "ERROR: TRUST_HOST is required. Usage: make test-trust-https TRUST_HOST=<ip-or-hostname>"; exit 1)
	@echo "--- HTTPS health check: https://$(TRUST_HOST):$(TRUST_API_PORT)/health ---"
	curl -sf --cacert certs/trust-ca.crt https://$(TRUST_HOST):$(TRUST_API_PORT)/health
