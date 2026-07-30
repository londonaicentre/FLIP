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

.PHONY: build up down up-xnat down-xnat xnat-shell xnat-stack-down clean \
	xnat-war-download xnat-plugins-download xnat-reset xnat-configure \
	create-xnat-network test unit_test local_test

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
# KIT selects the trust — now the trust CODE; the kit file is
# trust/.env.<CODE>.<env> (ENV derived from PROD, mirroring trust/Makefile),
# with a legacy fallback to trust/.env.<KIT> for the not-yet-migrated
# EC2/on-prem path (KIT=<slot>, no env suffix). That kit file carries this
# trust's XNAT_PORT / PACS_UI_PORT (unsuffixed) and its hub-assigned
# FL_KIT_SLOT_NUMBER — the same host-local profile trust/Makefile reads. KIT
# has no default so non-KIT targets (build, test) don't spuriously -include a
# file. The XNAT swarm stack name and overlay network are derived from the
# assigned slot NUMBER (not the CODE), so XNAT co-resides on the same
# deploy_trust-network-<N> as the slot-numbered trust core services.
ifeq ($(PROD),true)
ENV := production
else ifeq ($(PROD),stag)
ENV := stag
else
ENV := development
endif
KIT_FILE := $(if $(wildcard ../.env.$(KIT).$(ENV)),../.env.$(KIT).$(ENV),../.env.$(KIT))
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
# Slot NUMBER the hub assigned this trust, read from the kit file — NOT a
# substring of the CODE. Drives the stack name (xnat<N>) and overlay network
# (deploy_trust-network-<N>); trust/Makefile derives TRUST_NUM from
# FL_KIT_SLOT_NUMBER identically, so XNAT and the trust core services land on
# the same network.
TRUST_NUM := $(FL_KIT_SLOT_NUMBER)
XNAT_STACK := xnat$(TRUST_NUM)
XNAT_NETWORK := deploy_trust-network-$(TRUST_NUM)
# Defaults are the Trust_1 allocation, so a single-trust host (EC2 / on-prem)
# needs no XNAT port entries in its kit file.
XNAT_PORT_EFFECTIVE := $(or $(XNAT_PORT),8104)
PACS_UI_PORT_EFFECTIVE := $(or $(PACS_UI_PORT),8042)

# Host path that backs this trust's XNAT bind mounts (parent of xnat-data/
# and xnat-db-data/). Sourced from the kit file's Host-local profile;
# falls back to a per-trust default when the kit omits it:
#   stag/prod → /opt/flip/xnat-trust$(TRUST_NUM)   (per-slot dir; isolates
#               xnat1 / xnat2 when multiple trusts share one EC2 host)
#   dev       → ./xnat-data-trust$(TRUST_NUM)   (per-stack local dir; isolates
#               concurrent Trust_1 / Trust_2 stacks on the same host)
# Override per-host (e.g. on a box where /opt is small and the XNAT archive
# lives on a larger /data volume) by setting XNAT_DATA_DIR in
# trust/.env.<KIT> or exporting it before invoking make. Resolved to an
# absolute path so docker-compose-stack.{development,production}.yml can
# bind-mount it directly without a separate `${XNAT_PATH}/` prefix. Both
# this recipe and the compose stacks read the same exported var so the
# dirs Make creates and the dirs compose mounts stay in lockstep.
XNAT_DATA_DIR ?= $(if $(filter true stag,$(PROD)),/opt/flip/xnat-trust$(TRUST_NUM),./xnat-data-trust$(TRUST_NUM))
XNAT_DATA_DIR := $(abspath $(XNAT_DATA_DIR))
export XNAT_DATA_DIR

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

# Deploy-time guard: every XNAT password the compose stacks substitute is a
# bare ${VAR} with no default (docker-compose-stack.yml), so an empty value
# silently brings up a broken/insecure XNAT — admin auth 401s ("Site has not
# yet been configured" forever, see the export note above) and Postgres comes
# up with a blank superuser password — instead of failing. Refuse to deploy if
# any are empty. Covers both sources: XNAT_DATASOURCE_* (trust/xnat/.env) and
# XNAT_ADMIN_*/XNAT_SERVICE_* (the kit file). Checks the exported process env
# (the same values `docker stack deploy` substitutes), not just Make vars.
XNAT_REQUIRED_PASSWORDS := XNAT_ADMIN_INITIAL_PASSWORD XNAT_ADMIN_PASSWORD \
	XNAT_SERVICE_PASSWORD XNAT_DATASOURCE_PASSWORD XNAT_DATASOURCE_ADMIN_PASSWORD
define require_xnat_passwords
	@missing=""; \
	for v in $(XNAT_REQUIRED_PASSWORDS); do \
		if [ -z "$$(printenv $$v 2>/dev/null)" ]; then missing="$$missing $$v"; fi; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "❌ Refusing to deploy XNAT for KIT=$(KIT): empty password(s):$$missing"; \
		echo "   These are substituted into the compose stack with no default, so an empty"; \
		echo "   value yields a broken/insecure XNAT. Set XNAT_DATASOURCE_* in trust/xnat/.env"; \
		echo "   and XNAT_ADMIN_*/XNAT_SERVICE_* in the kit file ($(KIT_FILE)), then retry."; \
		exit 1; \
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

# Bring up every registered dev XNAT stack — the trust/.env.*.$(ENV) kits ARE
# the list (mirrors trust/Makefile's `up`). Each is a separate sub-make so its
# `-include ../.env.<CODE>.<env>` loads only that trust's kit file. `up-xnat`
# first blocks on `down-xnat` (via `xnat-stack-down`) before `xnat-reset` —
# otherwise a still-running xnat-web container holds the bind-mounted data
# dir's inode and the `sudo rm -rf` in `xnat-reset` silently leaves archive
# writes broken ("Source ... does not exist") so DICOM import fails on the
# next boot.
up:
	@found=0; for f in ../.env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#../.env.}; kit=$${kit%.$(ENV)}; found=1; \
	  $(MAKE) up-xnat KIT=$$kit; \
	done; \
	[ "$$found" = 1 ] || echo "ℹ️  No trust/.env.*.$(ENV) kits found — run 'make register-trusts' (from repo root) first."

# Bring up the XNAT swarm stack for one trust (KIT=Trust_1 / Trust_2).
# trust/Makefile's up-trust calls this so a trust comes up complete.
up-xnat: create-xnat-network
	$(require_kit)
	$(require_xnat_passwords)
	$(MAKE) down-xnat KIT=$(KIT)
	$(MAKE) xnat-reset KIT=$(KIT)
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=$(XNAT_PORT_EFFECTIVE) \
	PACS_UI_PORT=$(PACS_UI_PORT_EFFECTIVE) \
	DOCKER_NETWORK_NAME=$(XNAT_NETWORK) \
	docker stack deploy --with-registry-auth --detach=false ${STACK_FILES} $(XNAT_STACK)
	$(MAKE) xnat-configure XNAT_PROJECT=$(XNAT_STACK)

down:
	@for f in ../.env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#../.env.}; kit=$${kit%.$(ENV)}; \
	  $(MAKE) down-xnat KIT=$$kit; \
	done

down-xnat:
	$(require_kit)
	$(MAKE) xnat-stack-down STACK=$(XNAT_STACK)

# Get a shell in the running xnat-web container for the selected trust.
xnat-shell:
	$(require_kit)
	CONTAINER=$$(docker ps --filter "name=$(XNAT_STACK)_xnat-web" -q); \
	docker exec -it $$CONTAINER bash

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
		aws s3 cp "s3://$(FLIP_ARTIFACTS_BUCKET_NAME)/xnat-$(XNAT_VERSION)/xnat-web-$(XNAT_VERSION).war" \
			"xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war"; \
	fi

# Ensure required XNAT plugins are present locally (skips S3 sync if all are available).
# The S3 artifact set is keyed by XNAT version — s3://<bucket>/xnat-<version>/{xnat-web-<version>.war,
# plugins/} — so different branches (e.g. main vs develop across an XNAT upgrade) can build against
# different WAR + plugin rosters without clobbering each other.
xnat-plugins-download:
	@./scripts/ensure_plugins.sh "xnat/plugins" "$(FLIP_ARTIFACTS_BUCKET_NAME)" "xnat-$(XNAT_VERSION)/plugins"

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
		xnat_endpoint="$$(docker context inspect "$$xnat_ctx" --format '{{.Endpoints.docker.Host}}' 2>/dev/null)"; \
		case "$$xnat_endpoint" in \
			ssh://*) \
				echo "  🛰️  remote swarm deploy (context '$$xnat_ctx') — resetting XNAT dirs on the trust host via ssh"; \
				ssh "$$xnat_ctx" "sudo rm -rf $(XNAT_DATA_DIR) && sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/tomcat_logs $(XNAT_DATA_DIR)/xnat-data/archive $(XNAT_DATA_DIR)/xnat-data/build $(XNAT_DATA_DIR)/xnat-data/cache $(XNAT_DATA_DIR)/xnat-db-data && sudo chown -R 1000:1000 $(XNAT_DATA_DIR)"; \
				;; \
			*) \
				echo "  💻 local docker context '$$xnat_ctx' ($$xnat_endpoint) — resetting XNAT dirs on this host"; \
				sudo rm -rf $(XNAT_DATA_DIR); \
				sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/tomcat_logs; \
				sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/archive; \
				sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/build; \
				sudo mkdir -p $(XNAT_DATA_DIR)/xnat-data/cache; \
				sudo mkdir -p $(XNAT_DATA_DIR)/xnat-db-data; \
				sudo chown -R $(USER):$$(id -gn) $(XNAT_DATA_DIR); \
				;; \
		esac; \
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
	[ -n "$$CONTAINER" ] || { echo "❌ ERROR: $(XNAT_PROJECT)_xnat-web container not found at configure time"; exit 1; }; \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-xnat-$(XNAT_PROJECT).log" && bash configure-xnat.sh 2>&1 | tee "$$LOG_FILE"' && \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-dcm2niix-$(XNAT_PROJECT).log" && bash configure-dcm2niix.sh 2>&1 | tee "$$LOG_FILE"'

create-xnat-network:
	$(require_kit)
	@echo "🔧 Ensuring external Docker network $(XNAT_NETWORK) exists..."
	@docker network inspect $(XNAT_NETWORK) >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable $(XNAT_NETWORK)

# Anonymization-script tests (pure Python; no XNAT or Docker required)
test unit_test local_test:
	$(MAKE) -C tests $@
