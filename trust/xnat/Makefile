# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

.PHONY: build up down clean xnat-shell xnat-plugins-download xnat-plugins-clean \
	xnat-reset xnat-configure-trust-1 xnat-configure-trust-2 \
	create-network-trust-1 create-network-trust-2 up-xnat-1 up-xnat-2

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

# Load .env file inside xnat-docker-compose folder
ifneq ("$(wildcard xnat-docker-compose/.env)","")
include xnat-docker-compose/.env
export $(shell sed 's/=.*//' xnat-docker-compose/.env)
endif

DOCKER_COMPOSE_CMD=docker compose -f docker-compose.yml
DOCKER_COMPOSE_PROD_CMD=docker compose -f docker-compose.production.yml

project1 = xnat1
project2 = xnat2
network1 = deploy_trust-network-1
network2 = deploy_trust-network-2

# Build the Docker images using the compose file
build: xnat-war-download xnat-plugins-download
	@echo "🔧 Building XNAT Docker images (no cache)..."
	cd xnat-docker-compose && \
	${DOCKER_COMPOSE_CMD} build --no-cache

# Path to the current folder (docker stack deploy needs absolute path)
export XNAT_PATH=$(realpath .)

TRUST_1_VARS = \
	TRUST_NETWORK_NAME=${network1} \
	XNAT_PORT=${XNAT_PORT_TRUST_1}
TRUST_2_VARS = \
	TRUST_NETWORK_NAME=${network2} \
	XNAT_PORT=${XNAT_PORT_TRUST_2}

up: xnat-reset up-xnat-1 up-xnat-2

up-xnat-1: create-network-trust-1
	cd xnat-docker-compose && \
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -p ${project1} up -d --remove-orphans
	$(MAKE) xnat-configure-trust-1

up-xnat-1-stag: create-network-trust-1
	cd xnat-docker-compose && \
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_PROD_CMD} -p ${project1} up -d --remove-orphans --pull always
	$(MAKE) xnat-configure-trust-1

up-xnat-2: create-network-trust-2
	cd xnat-docker-compose && \
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${project2} up -d --remove-orphans
	$(MAKE) xnat-configure-trust-2

# Get a shell in the running xnat container (for project1)
xnat-shell:
	cd xnat-docker-compose && docker compose -p ${project1} exec xnat /bin/sh

# Stop all containers
down:
	cd xnat-docker-compose && \
	docker compose -p ${project1} down && \
	docker compose -p ${project2} down

# Stop and clean up images
clean:
	cd xnat-docker-compose && \
	docker compose -p ${project1} down --rmi local && \
	docker compose -p ${project2} down --rmi local

# Download XNAT WAR into the xnat-web Docker build context
xnat-war-download:
	@if [ -f xnat-docker-compose/xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war ]; then \
		echo "✅ XNAT WAR already exists locally."; \
	else \
		echo "📦 Downloading XNAT WAR from S3..."; \
		aws s3 cp "s3://flipdev-artifacts/xnat/xnat-web-$(XNAT_VERSION).war" \
			"xnat-docker-compose/xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war"; \
		ls -lh "xnat-docker-compose/xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war"; \
	fi

# Remove any older plugin jars from the plugins folder
xnat-plugins-clean:
	@echo "🧹 Cleaning local XNAT plugin cache (keeping only desired versions)"
	@cd xnat-docker-compose/xnat/plugins && rm -f *.jar && echo "✅ Plugin cache cleaned."

# Plugin versions (see the compatibility matrices for each plugin, e.g. for the container service plugin, 
# see https://wiki.xnat.org/container-service/container-service-compatibility-matrix)
xnat-plugins-download:
	@echo "📦 Downloading required XNAT plugins if missing..."
	@aws s3 sync s3://flipdev-artifacts/xnat/plugins/ xnat-docker-compose/xnat/plugins/

# Note this needs root privileges to run
# Creates directory structure for XNAT (Docker Swarm requires them to exist beforehand)
xnat-reset:
	echo "Deleting previous XNAT data ..."; \
	sudo rm -rf xnat-docker-compose/${XNAT_PORT_TRUST_1}; \
	sudo rm -rf xnat-docker-compose/${XNAT_PORT_TRUST_2}; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_1}/xnat-data/tomcat_logs; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_1}/xnat-data/archive; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_1}/xnat-data/build; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_1}/xnat-data/cache; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_2}/xnat-data/tomcat_logs; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_2}/xnat-data/archive; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_2}/xnat-data/build; \
	mkdir -p xnat-docker-compose/${XNAT_PORT_TRUST_2}/xnat-data/cache;

# Configure XNAT instances with initial settings and run dcm2niix setup script
xnat-configure-trust-1:
	cd xnat-docker-compose && \
	docker compose -p ${project1} exec xnat-web bash -c "cd /data/xnat/config && bash configure-xnat.sh" && \
	docker compose -p ${project1} exec xnat-web bash -c "cd /data/xnat/config && bash configure-dcm2niix.sh"

xnat-configure-trust-2:
	cd xnat-docker-compose && \
	docker compose -p ${project2} exec xnat-web bash -c "cd /data/xnat/config && bash configure-xnat.sh" && \
	docker compose -p ${project2} exec xnat-web bash -c "cd /data/xnat/config && bash configure-dcm2niix.sh"

create-network-trust-1:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network1} >/dev/null 2>&1 || \
		docker network create --driver bridge ${network1}

create-network-trust-2:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network2} >/dev/null 2>&1 || \
		docker network create --driver bridge ${network2}


# ================== SWARM =======================

up-swarm: xnat-reset up-xnat-1-swarm up-xnat-2-swarm

up-xnat-1-swarm: create-network-trust-1-swarm
	cd xnat-docker-compose && \
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=${XNAT_PORT_TRUST_1} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_1} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_1} \
	DOCKER_NETWORK_NAME=${network1} \
	docker stack deploy --with-registry-auth -c docker-compose-stack.yml ${project1}
# 	wait for a few seconds to ensure the containers are up before configuring
	sleep 10
	$(MAKE) xnat-configure-trust-1-swarm

up-xnat-2-swarm: create-network-trust-2-swarm
	cd xnat-docker-compose && \
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=${XNAT_PORT_TRUST_2} \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_2} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_2} \
	DOCKER_NETWORK_NAME=${network2} \
	docker stack deploy --with-registry-auth -c docker-compose-stack.yml ${project2}
# 	wait for a few seconds to ensure the containers are up before configuring
	sleep 10
	$(MAKE) xnat-configure-trust-2-swarm

# Get a shell in the running xnat container (for project1) - swarm mode
xnat-shell-swarm:
	CONTAINER=$$(docker ps --filter "name=${project1}_xnat-web" -q); \
	docker exec -it $$CONTAINER bash

# Stop all containers
down-swarm:
	cd xnat-docker-compose && \
	docker stack rm ${project1} && \
	docker stack rm ${project2}

# Stop and clean up images (swarm)
clean-swarm:
	cd xnat-docker-compose && \
	docker stack rm ${project1} && \
	docker stack rm ${project2} && \
	docker system prune -f

# Configure both XNAT instances in swarm mode
# find the container ID and run the configuration scripts inside it
xnat-configure-trust-1-swarm:
	@echo "⏳ Waiting for ${project1}_xnat-web container to be ready..."
	@timeout 120 bash -c 'until [ "$$(docker ps --filter "name=${project1}_xnat-web" -q)" != "" ]; do echo "  Waiting for container..."; sleep 5; done' || { echo "❌ ERROR: ${project1}_xnat-web container never started"; exit 1; }
	CONTAINER=$$(docker ps --filter "name=${project1}_xnat-web" -q); \
	docker exec $$CONTAINER bash -c "cd /data/xnat/config && bash configure-xnat.sh"; \
	docker exec $$CONTAINER bash -c "cd /data/xnat/config && bash configure-dcm2niix.sh"

xnat-configure-trust-2-swarm:
	@echo "⏳ Waiting for ${project2}_xnat-web container to be ready..."
	@timeout 120 bash -c 'until [ "$$(docker ps --filter "name=${project2}_xnat-web" -q)" != "" ]; do echo "  Waiting for container..."; sleep 5; done' || { echo "❌ ERROR: ${project2}_xnat-web container never started"; exit 1; }
	CONTAINER=$$(docker ps --filter "name=${project2}_xnat-web" -q); \
	docker exec $$CONTAINER bash -c "cd /data/xnat/config && bash configure-xnat.sh"; \
	docker exec $$CONTAINER bash -c "cd /data/xnat/config && bash configure-dcm2niix.sh"

create-network-trust-1-swarm:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network1} >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable ${network1}

create-network-trust-2-swarm:
	@echo "🔧 Ensuring external Docker networks exist..."
	@docker network inspect ${network2} >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable ${network2}
