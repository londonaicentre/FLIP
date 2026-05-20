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

.PHONY: build up down up-xnat down-xnat up-xnat-local down-xnat-local xnat-shell \
	xnat-war-download xnat-plugins-download xnat-reset xnat-configure xnat-configure-local \
	create-xnat-network create-network-trust-local test unit_test local_test

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

# XNAT images often lag the rest of the stack — branch builds for trust-api /
# imaging-api / data-access-api / orthanc are cheap (small images, fast builds)
# but the XNAT images are large and slow to rebuild for ad-hoc branches.
# XNAT_TAG lets a deploy pin the XNAT layer at a known-good tag (e.g. stag)
# while the rest of the stack runs a feature-branch tag via DOCKER_TAG.
# Defaults to DOCKER_TAG so existing single-tag deploys are unchanged.
XNAT_TAG ?= ${DOCKER_TAG}
export XNAT_TAG

# ── Per-trust XNAT stack ──────────────────────────────────────────────────
# KIT selects the trust — the kit-file name under trust/.env.<KIT>. That kit
# file carries this trust's XNAT_PORT / PACS_UI_PORT (unsuffixed), the same
# host-local profile trust/Makefile reads. KIT has no default so non-KIT
# targets (build, test) don't spuriously -include a file. The XNAT swarm
# stack name and overlay network are derived from the kit's trust number so
# the existing xnat<N> / deploy_trust-network-<N> scheme is preserved.
KIT_FILE := ../.env.$(KIT)
-include $(KIT_FILE)
TRUST_NUM := $(subst Trust_,,$(KIT))
XNAT_STACK := xnat$(TRUST_NUM)
XNAT_NETWORK := deploy_trust-network-$(TRUST_NUM)
# Defaults are the Trust_1 allocation, so a single-trust host (EC2 / on-prem)
# needs no XNAT port entries in its kit file.
XNAT_PORT_EFFECTIVE := $(or $(XNAT_PORT),8104)
PACS_UI_PORT_EFFECTIVE := $(or $(PACS_UI_PORT),8042)

project_local = xnat-local
network_local = deploy_trust-network-local

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

# Guard: a KIT-selected target needs KIT set.
define require_kit
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi
endef

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
		-t ${DOCKER_REGISTRY}xnat-web:${DOCKER_TAG} \
		xnat/
	@echo "🐳 Building xnat-db image..."
	docker build \
		--build-arg PG_VERSION=${PG_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-db:${DOCKER_TAG} \
		postgres/
	@echo "🐳 Building xnat-nginx image..."
	docker build \
		--build-arg NGINX_VERSION=${NGINX_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-nginx:${DOCKER_TAG} \
		nginx/
	@echo "✅ XNAT Docker images built successfully!"

# Bring up both dev XNAT stacks. Each is a separate sub-make so its
# `-include ../.env.<KIT>` loads only that trust's kit file.
up:
	$(MAKE) up-xnat KIT=Trust_1
	$(MAKE) up-xnat KIT=Trust_2

# Bring up the XNAT swarm stack for one trust (KIT=Trust_1 / Trust_2).
# trust/Makefile's up-trust calls this so a trust comes up complete.
up-xnat: create-xnat-network
	$(require_kit)
	$(MAKE) xnat-reset KIT=$(KIT)
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=$(XNAT_PORT_EFFECTIVE) \
	PACS_UI_PORT=$(PACS_UI_PORT_EFFECTIVE) \
	DOCKER_NETWORK_NAME=$(XNAT_NETWORK) \
	docker stack deploy --with-registry-auth --detach=false ${STACK_FILES} $(XNAT_STACK)
	$(MAKE) xnat-configure XNAT_PROJECT=$(XNAT_STACK)

down:
	$(MAKE) down-xnat KIT=Trust_1
	$(MAKE) down-xnat KIT=Trust_2

down-xnat:
	$(require_kit)
	docker stack rm $(XNAT_STACK) || true

# Get a shell in the running xnat-web container for the selected trust.
xnat-shell:
	$(require_kit)
	CONTAINER=$$(docker ps --filter "name=$(XNAT_STACK)_xnat-web" -q); \
	docker exec -it $$CONTAINER bash

up-xnat-local: create-network-trust-local
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=8108 \
	PACS_UI_PORT=8046 \
	DOCKER_NETWORK_NAME=${network_local} \
	docker stack deploy --with-registry-auth --detach=false ${STACK_FILES} ${project_local}
	$(MAKE) xnat-configure XNAT_PROJECT=${project_local}

down-xnat-local:
	docker stack rm ${project_local}

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

# Creates the data-volume directory structure for one trust's XNAT stack and
# wipes any previous data. Docker Swarm requires host directories to exist
# before bind-mounting; the wipe avoids stale bind mounts from a prior run
# (the second xnat-web otherwise crash-loops on a locked SQLite index).
# In production/staging the path is /opt/flip/xnat/; in development it is
# ./[XNAT_PORT]/xnat-data, keyed by this trust's XNAT port.
xnat-reset:
	$(require_kit)
	@case "$$(pwd)" in */trust/xnat) ;; *) \
		echo "ERROR: xnat-reset must be run from the trust/xnat/ directory (pwd: $$(pwd))"; exit 1;; \
	esac
	@if ! echo "$(XNAT_PORT_EFFECTIVE)" | grep -qE '^[0-9]+$$'; then \
		echo "ERROR: XNAT_PORT must be numeric for KIT=$(KIT), got '$(XNAT_PORT_EFFECTIVE)'"; exit 1; \
	fi
	@echo "Deleting previous XNAT data and creating new directories for $(KIT) ..."; \
	if [ "$(PROD)" = "true" ] || [ "$(PROD)" = "stag" ]; then \
		echo "  📍 Using production path: /opt/flip/xnat/"; \
		sudo rm -rf /opt/flip/xnat; \
		sudo mkdir -p /opt/flip/xnat/xnat-data/tomcat_logs; \
		sudo mkdir -p /opt/flip/xnat/xnat-data/archive; \
		sudo mkdir -p /opt/flip/xnat/xnat-data/build; \
		sudo mkdir -p /opt/flip/xnat/xnat-data/cache; \
		sudo mkdir -p /opt/flip/xnat/xnat-db-data; \
		sudo chown -R $(USER):$(USER) /opt/flip/xnat; \
	else \
		echo "  📍 Using development path: ./$(XNAT_PORT_EFFECTIVE)/xnat-data"; \
		sudo rm -rf $(XNAT_PORT_EFFECTIVE); \
		mkdir -p $(XNAT_PORT_EFFECTIVE)/xnat-data/tomcat_logs; \
		mkdir -p $(XNAT_PORT_EFFECTIVE)/xnat-data/archive; \
		mkdir -p $(XNAT_PORT_EFFECTIVE)/xnat-data/build; \
		mkdir -p $(XNAT_PORT_EFFECTIVE)/xnat-data/cache; \
	fi

# Configure an XNAT instance with initial settings and run dcm2niix setup.
# Usage: make xnat-configure XNAT_PROJECT=<stack-name>
xnat-configure:
	@[ -n "$(XNAT_PROJECT)" ] || (echo "❌ XNAT_PROJECT is required"; exit 1)
	@echo "⏳ Waiting for $(XNAT_PROJECT)_xnat-web container to be ready..."
	@elapsed=0; while [ "$$(docker ps --filter "name=$(XNAT_PROJECT)_xnat-web" -q)" = "" ]; do \
		if [ $$elapsed -ge 120 ]; then echo "❌ ERROR: $(XNAT_PROJECT)_xnat-web container never started"; exit 1; fi; \
		echo "  Waiting for container..."; sleep 5; elapsed=$$((elapsed + 5)); \
	done
	CONTAINER=$$(docker ps --filter "name=$(XNAT_PROJECT)_xnat-web" -q); \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-xnat-$(XNAT_PROJECT).log" && bash configure-xnat.sh 2>&1 | tee "$$LOG_FILE"'; \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-dcm2niix-$(XNAT_PROJECT).log" && bash configure-dcm2niix.sh 2>&1 | tee "$$LOG_FILE"'

xnat-configure-local:
	$(MAKE) xnat-configure XNAT_PROJECT=${project_local}

create-xnat-network:
	$(require_kit)
	@echo "🔧 Ensuring external Docker network $(XNAT_NETWORK) exists..."
	@docker network inspect $(XNAT_NETWORK) >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable $(XNAT_NETWORK)

create-network-trust-local:
	@echo "🔧 Ensuring external Docker network ${network_local} exists..."
	@docker network inspect ${network_local} >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable ${network_local}

# Anonymization-script tests (pure Python; no XNAT or Docker required)
test unit_test local_test:
	$(MAKE) -C tests $@
