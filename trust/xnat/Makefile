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

.PHONY: build up down up-xnat down-xnat up-xnat-local down-xnat-local xnat-shell xnat-stack-down clean \
	xnat-war-download xnat-plugins-download xnat-reset xnat-configure xnat-configure-local \
	create-xnat-network create-network-trust-local test unit_test local_test

# Mirrors trust/Makefile: no hub `.env.*` include. Runtime config (DOCKER_REGISTRY,
# DOCKER_TAG, XNAT_PORT, XNAT_ADMIN_*, XNAT_SERVICE_*, XNAT_DATABASE_URL) is
# pulled from the kit file via `-include $(KIT_FILE)` below; XNAT-internal
# build args (XNAT_VERSION, XNAT_DATASOURCE_*, XNAT_SMTP_*, heap, paths) come
# from trust/xnat/.env. The dev-admin build path (xnat-war-download /
# xnat-plugins-download) needs FLIP_ARTIFACTS_BUCKET_NAME — pass it on the
# CLI if invoking those targets directly.

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

# XNAT_DATA_DIR is set AFTER the kit-file include below so the kit's value
# wins over the make-side default.

# ── Per-trust XNAT stack ──────────────────────────────────────────────────
# KIT selects the trust — the kit-file name under trust/.env.<KIT>. That kit
# file carries this trust's XNAT_PORT / PACS_UI_PORT (unsuffixed), the same
# host-local profile trust/Makefile reads. KIT has no default so non-KIT
# targets (build, test) don't spuriously -include a file. The XNAT swarm
# stack name and overlay network are derived from the kit's trust number so
# the existing xnat<N> / deploy_trust-network-<N> scheme is preserved.
KIT_FILE := ../.env.$(KIT)
-include $(KIT_FILE)
# Kit-file values must reach the shell env, not just Make vars. docker stack
# deploy (unlike docker compose) has no --env-file flag; it substitutes
# ${XNAT_ADMIN_USER}, ${PACS_DICOM_PORT}, ${DOCKER_REGISTRY}, etc. in the
# compose YAML from the calling shell env only. Without this export, the
# stack came up with empty admin creds and configure-xnat.sh would 401/403
# on every auth call, leaving "Site has not yet been configured" forever.
# Pattern mirrors line 28 for trust/xnat/.env; Make silently ignores the
# comment-line "exports" that sed leaks through.
ifneq ("$(wildcard $(KIT_FILE))","")
export $(shell sed 's/=.*//' $(KIT_FILE))
endif
TRUST_NUM := $(subst Trust_,,$(KIT))
XNAT_STACK := xnat$(TRUST_NUM)
XNAT_NETWORK := deploy_trust-network-$(TRUST_NUM)
# Defaults are the Trust_1 allocation, so a single-trust host (EC2 / on-prem)
# needs no XNAT port entries in its kit file.
XNAT_PORT_EFFECTIVE := $(or $(XNAT_PORT),8104)
PACS_UI_PORT_EFFECTIVE := $(or $(PACS_UI_PORT),8042)

# Host path that backs this trust's XNAT bind mounts (parent of xnat-data/
# and xnat-db-data/). Sourced from the kit file's Host-local profile;
# falls back to a per-trust default when the kit omits it:
#   stag/prod → /opt/flip/xnat   (what the Ansible provisioner creates)
#   dev       → ./xnat-data-trust$(TRUST_NUM)   (per-stack local dir; isolates
#               concurrent Trust_1 / Trust_2 stacks on the same host)
# Override per-host (e.g. on a box where /opt is small and the XNAT archive
# lives on a larger /data volume) by setting XNAT_DATA_DIR in
# trust/.env.<KIT> or exporting it before invoking make. Resolved to an
# absolute path so docker-compose-stack.{development,production}.yml can
# bind-mount it directly without a separate `${XNAT_PATH}/` prefix. Both
# this recipe and the compose stacks read the same exported var so the
# dirs Make creates and the dirs compose mounts stay in lockstep.
XNAT_DATA_DIR ?= $(if $(filter true stag,$(PROD)),/opt/flip/xnat,./xnat-data-trust$(TRUST_NUM))
XNAT_DATA_DIR := $(abspath $(XNAT_DATA_DIR))
export XNAT_DATA_DIR

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
# `-include ../.env.<KIT>` loads only that trust's kit file. `up-xnat` first
# blocks on `down-xnat` (via `xnat-stack-down`) before `xnat-reset` — otherwise
# a still-running xnat-web container holds the bind-mounted data dir's inode
# and the `sudo rm -rf` in `xnat-reset` silently leaves archive writes broken
# ("Source ... does not exist") so DICOM import fails on the next boot.
up:
	$(MAKE) up-xnat KIT=Trust_1
	$(MAKE) up-xnat KIT=Trust_2

# Bring up the XNAT swarm stack for one trust (KIT=Trust_1 / Trust_2).
# trust/Makefile's up-trust calls this so a trust comes up complete.
up-xnat: create-xnat-network
	$(require_kit)
	$(MAKE) down-xnat KIT=$(KIT)
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
	$(MAKE) xnat-stack-down STACK=$(XNAT_STACK)

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

# Remove a single XNAT Swarm stack and BLOCK until all its containers have
# stopped. `docker stack rm` is asynchronous; callers that then delete the
# stack's bind-mounted host directories (see `xnat-reset`) must wait for the
# containers to release those mounts first.
# Usage: $(MAKE) xnat-stack-down STACK=<stack-name>
xnat-stack-down:
	@[ -n "$(STACK)" ] || (echo "❌ STACK is required"; exit 1)
	@echo "🧹 Removing XNAT stack $(STACK) if present..."
	@docker stack rm $(STACK) 2>/dev/null || true
	@elapsed=0; \
	while [ -n "$$(docker ps -aq --filter label=com.docker.stack.namespace=$(STACK) 2>/dev/null)" ]; do \
		if [ $$elapsed -ge 120 ]; then \
			echo "❌ ERROR: $(STACK) containers still running after 120s"; exit 1; \
		fi; \
		echo "⏳ Waiting for $(STACK) containers to stop ($${elapsed}s)..."; \
		sleep 2; elapsed=$$((elapsed + 2)); \
	done
	@echo "✅ $(STACK) fully removed."

down-xnat-local:
	$(MAKE) xnat-stack-down STACK=${project_local}

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
# Data dir defaults vary by PROD — see the XNAT_DATA_DIR comment above
# for the resolution rule and how to override per-host.
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
		echo "  📍 Using prod data dir: $(XNAT_DATA_DIR)/"; \
		xnat_ctx="$$(docker context show)"; \
		if [ "$$xnat_ctx" != "default" ]; then \
			echo "  🛰️  remote swarm deploy (context '$$xnat_ctx') — resetting XNAT dirs on the trust host via ssh"; \
			ssh "$$xnat_ctx" "sudo rm -rf $(XNAT_DATA_DIR) && sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/tomcat_logs $(XNAT_DATA_DIR)/xnat-data/archive $(XNAT_DATA_DIR)/xnat-data/build $(XNAT_DATA_DIR)/xnat-data/cache $(XNAT_DATA_DIR)/xnat-db-data && sudo chown -R 1000:1000 $(XNAT_DATA_DIR)"; \
		else \
			sudo rm -rf $(XNAT_DATA_DIR); \
			sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/tomcat_logs; \
			sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/archive; \
			sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/build; \
			sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/cache; \
			sudo mkdir -p $(XNAT_DATA_DIR)/xnat-db-data; \
			sudo chown -R $(USER):$(USER) $(XNAT_DATA_DIR); \
		fi; \
	else \
		echo "  📍 Using dev data dir: $(XNAT_DATA_DIR)/"; \
		sudo rm -rf $(XNAT_DATA_DIR); \
		mkdir -p $(XNAT_DATA_DIR)/xnat-data/tomcat_logs; \
		mkdir -p $(XNAT_DATA_DIR)/xnat-data/archive; \
		mkdir -p $(XNAT_DATA_DIR)/xnat-data/build; \
		mkdir -p $(XNAT_DATA_DIR)/xnat-data/cache; \
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
