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

.PHONY: build up down clean xnat-shell xnat-war-download xnat-plugins-download \
	xnat-reset xnat-configure-trust-1 xnat-configure-trust-2 \
	create-network-trust-1 create-network-trust-2 up-xnat-1 up-xnat-2 \
	down-xnat-1 down-xnat-2

# if tag PROD=true is passed, set PROD=true
ifeq ($(PROD),true)
MAIN_ENV_FILE=../../.env.production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=../../.env.stag
else
MAIN_ENV_FILE=../../.env.development
endif

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

# Load XNAT-specific .env file
ifneq ("$(wildcard .env)","")
include .env
export $(shell sed 's/=.*//' .env)
endif

# Default DOCKER_TAG to 'stag' when running standalone (not via root Makefile which exports its own)
DOCKER_TAG ?= stag
export DOCKER_TAG

project1 = xnat1
project2 = xnat2
network1 = deploy_trust-network-1
network2 = deploy_trust-network-2

# Path to the dev XNAT folder on the Docker host (bind mounts resolve there, not locally).
export XNAT_PATH=$(realpath .)

ifeq ($(PROD),true)
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.production.yml
else ifeq ($(PROD),stag)
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.production.yml
else
# In development, mount plugins and config from the host for hot-reload
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.development.yml
endif

# Build XNAT Docker images locally (mirrors CI workflow)
# Requires AWS CLI access to download WAR and plugins from S3
build:
	@echo "🔧 Building XNAT Docker images..."
	$(MAKE) xnat-war-download
	$(MAKE) xnat-plugins-download
	@echo "🐳 Building xnat-web image..."
	docker build \
		--build-arg XNAT_VERSION=${XNAT_VERSION} \
		--build-arg XNAT_SMTP_ENABLED=${XNAT_SMTP_ENABLED} \
		--build-arg XNAT_DATASOURCE_DRIVER=${XNAT_DATASOURCE_DRIVER} \
		--build-arg XNAT_DATASOURCE_USERNAME=${XNAT_DATASOURCE_USERNAME} \
		--build-arg XNAT_DATASOURCE_PASSWORD=${XNAT_DATASOURCE_PASSWORD} \
		--build-arg XNAT_DATASOURCE_URL=${XNAT_DATASOURCE_URL} \
		--build-arg TOMCAT_XNAT_FOLDER=${TOMCAT_XNAT_FOLDER} \
		--build-arg XNAT_ROOT=${XNAT_ROOT} \
		--build-arg XNAT_HOME=${XNAT_HOME} \
		--build-arg XNAT_EMAIL=${XNAT_EMAIL} \
		-t ${DOCKER_REGISTRY}xnat-web:dev \
		xnat/
	@echo "🐳 Building xnat-db image..."
	docker build \
		--build-arg PG_VERSION=${PG_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-db:dev \
		postgres/
	@echo "🐳 Building xnat-nginx image..."
	docker build \
		--build-arg NGINX_VERSION=${NGINX_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-nginx:dev \
		nginx/
	@echo "✅ XNAT Docker images built successfully!"

up: xnat-reset up-xnat-1 up-xnat-2

up-xnat-1: create-network-trust-1
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=${XNAT_PORT_TRUST_1} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_1} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_1} \
	DOCKER_NETWORK_NAME=${network1} \
	docker stack deploy --with-registry-auth ${STACK_FILES} ${project1}
	$(MAKE) xnat-configure-trust-1

up-xnat-2: create-network-trust-2
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=${XNAT_PORT_TRUST_2} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_2} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_2} \
	DOCKER_NETWORK_NAME=${network2} \
	docker stack deploy --with-registry-auth ${STACK_FILES} ${project2}
	$(MAKE) xnat-configure-trust-2

# Get a shell in the running xnat container (for project1)
xnat-shell:
	CONTAINER=$$(docker ps --filter "name=${project1}_xnat-web" -q); \
	docker exec -it $$CONTAINER bash

down-xnat-1:
	docker stack rm ${project1}

down-xnat-2:
	docker stack rm ${project2}

down: down-xnat-1 down-xnat-2

# Stop and clean up images
clean: down
	docker system prune -f

# Download XNAT WAR into the xnat-web Docker build context (skips if already present)
xnat-war-download:
	@mkdir -p xnat/build-artifacts
	@if [ -f xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war ]; then \
		echo "✅ XNAT WAR already exists locally."; \
	else \
		echo "📦 Downloading XNAT WAR from S3..."; \
		aws s3 cp "s3://$(FLIP_ARTIFACTS_BUCKET_NAME)/xnat/xnat-web-$(XNAT_VERSION).war" \
			"xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war"; \
	fi

# Ensure required XNAT plugins are present locally (skips S3 sync if all are available)
xnat-plugins-download:
	@./scripts/ensure_plugins.sh "xnat/plugins" "$(FLIP_ARTIFACTS_BUCKET_NAME)"

# Creates directory structure for XNAT data volumes.
# Docker Swarm requires host directories to exist before bind-mounting.
# Note: needs root privileges to clean up previous data.
xnat-reset:
	@case "$$(pwd)" in */trust/xnat) ;; *) \
		echo "ERROR: xnat-reset must be run from the trust/xnat/ directory (pwd: $$(pwd))"; exit 1;; \
	esac
	@if ! echo "$(XNAT_PORT_TRUST_1)" | grep -qE '^[0-9]+$$'; then \
		echo "ERROR: XNAT_PORT_TRUST_1 must be set to a numeric value, got '$(XNAT_PORT_TRUST_1)'"; exit 1; \
	fi; \
	if ! echo "$(XNAT_PORT_TRUST_2)" | grep -qE '^[0-9]+$$'; then \
		echo "ERROR: XNAT_PORT_TRUST_2 must be set to a numeric value, got '$(XNAT_PORT_TRUST_2)'"; exit 1; \
	fi
	echo "Deleting previous XNAT data and creating new directories ..."; \
	sudo rm -rf ${XNAT_PORT_TRUST_1}; \
	sudo rm -rf ${XNAT_PORT_TRUST_2}; \
	mkdir -p ${XNAT_PORT_TRUST_1}/xnat-data/tomcat_logs; \
	mkdir -p ${XNAT_PORT_TRUST_1}/xnat-data/archive; \
	mkdir -p ${XNAT_PORT_TRUST_1}/xnat-data/build; \
	mkdir -p ${XNAT_PORT_TRUST_1}/xnat-data/cache; \
	mkdir -p ${XNAT_PORT_TRUST_2}/xnat-data/tomcat_logs; \
	mkdir -p ${XNAT_PORT_TRUST_2}/xnat-data/archive; \
	mkdir -p ${XNAT_PORT_TRUST_2}/xnat-data/build; \
	mkdir -p ${XNAT_PORT_TRUST_2}/xnat-data/cache;

# Configure XNAT instances with initial settings and run dcm2niix setup script
# Finds the container ID by name and runs the configuration scripts inside it
xnat-configure-trust-1:
	@echo "⏳ Waiting for ${project1}_xnat-web container to be ready..."
	@timeout 120 bash -c 'until [ "$$(docker ps --filter "name=${project1}_xnat-web" -q)" != "" ]; do echo "  Waiting for container..."; sleep 5; done' || { echo "❌ ERROR: ${project1}_xnat-web container never started"; exit 1; }
	CONTAINER=$$(docker ps --filter "name=${project1}_xnat-web" -q); \
	docker exec $$CONTAINER bash -c 'cd /data/xnat/config && LOG_FILE="configure-xnat-${project1}.log" && bash configure-xnat.sh 2>&1 | tee "$$LOG_FILE"'; \
	docker exec $$CONTAINER bash -c 'cd /data/xnat/config && LOG_FILE="configure-dcm2niix-${project1}.log" && bash configure-dcm2niix.sh 2>&1 | tee "$$LOG_FILE"'

xnat-configure-trust-2:
	@echo "⏳ Waiting for ${project2}_xnat-web container to be ready..."
	@timeout 120 bash -c 'until [ "$$(docker ps --filter "name=${project2}_xnat-web" -q)" != "" ]; do echo "  Waiting for container..."; sleep 5; done' || { echo "❌ ERROR: ${project2}_xnat-web container never started"; exit 1; }
	CONTAINER=$$(docker ps --filter "name=${project2}_xnat-web" -q); \
	docker exec $$CONTAINER bash -c 'cd /data/xnat/config && LOG_FILE="configure-xnat-${project2}.log" && bash configure-xnat.sh 2>&1 | tee "$$LOG_FILE"'; \
	docker exec $$CONTAINER bash -c 'cd /data/xnat/config && LOG_FILE="configure-dcm2niix-${project2}.log" && bash configure-dcm2niix.sh 2>&1 | tee "$$LOG_FILE"'

create-network-trust-1:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network1} >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable ${network1}

create-network-trust-2:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network2} >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable ${network2}
