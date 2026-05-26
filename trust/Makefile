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

.PHONY: build dev prod clean stop up down up-trust down-trust restart-trust up-trust-ec2 down-trust-ec2 \
	up-fl-clients down-fl-clients up-local-trust down-local-trust create-networks create-networks-local-trust \
	remove-networks recreate-networks tests update-omop-data update-orthanc-data integration_test
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

COMMON_COMPOSE_FILE := deploy/compose_trust.$(__DCKR_SUFFIX).yml
FL_BACKEND_COMPOSE_FILE := deploy/compose_trust.$(__DCKR_SUFFIX).$(FL_BACKEND).yml

# Resolve FL_PROVISIONED_DIR (from .env) to an absolute path relative to the repo root.
# Docker requires absolute paths for volume mounts; the .env value may be relative.
# Skip if already absolute (the parent Makefile resolves it before invoking sub-make).
MAKEFILE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
ifeq ($(patsubst /%,,$(FL_PROVISIONED_DIR)),$(FL_PROVISIONED_DIR))
override FL_PROVISIONED_DIR := $(abspath $(MAKEFILE_DIR)/../$(FL_PROVISIONED_DIR))
endif

network_local = deploy_trust-network-local
trust_local = trust-local

OMOP_DB_CMD=make -C ./omop-db
ORTHANC_CMD=make -C ./orthanc
# --project-directory . keeps relative paths inside the compose files (./trust-api,
# ./observability/, ./omop-db/volumes/, …) resolving from trust/ even though the compose
# files now live in trust/deploy/. Avoids having to rewrite every ./X to ../X.
DOCKER_COMPOSE_CMD=docker compose --project-directory . -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE)
DEBUG_OVERRIDE_COMPOSE_COMMAND=docker compose --project-directory . -f $(COMMON_COMPOSE_FILE) -f $(FL_BACKEND_COMPOSE_FILE) -f deploy/compose_trust.debug.override.yml

# NOTE DOCKER_FL_REGISTRY is set to empty when we use local FL images during development (e.g. flare-fl-client:dev).
# In that case, '--pull always' will error because docker won't be able to find the manifest for the dev image online,
# so we need to remove the --pull always flag. Non-FL images keep DOCKER_REGISTRY (always GHCR).
ifneq ($(strip $(DOCKER_FL_REGISTRY)),)
PULL_ALWAYS_FLAG=--pull always
else
PULL_ALWAYS_FLAG=
endif

# ── Per-trust deployment ──────────────────────────────────────────────────
# A trust stack is selected by KIT — the kit-file name under trust/.env.<KIT>
# (the FL kit slot name, e.g. Trust_1). trust/.env.<KIT> carries that trust's
# host-local profile (ports, bind dirs) and kit credentials (TRUST_API_KEY,
# TRUST_INTERNAL_SERVICE_KEY, FL_KIT_SLOT, FL_KIT_SLOT_NUMBER, EXPECTED_TRUST_ID);
# `make register-trusts` writes it, trust/.env.<KIT>.example is the
# template. KIT has no default so up-local-trust (which selects its own kit
# via LOCAL_TRUST_NAME) never double-includes — pass KIT=<name> explicitly.
#
# The defaults in TRUST_VARS are the Trust_1 port allocation: a single-trust
# host (EC2 / on-prem) needs no port entries in its kit file, while a second
# dev stack overrides them via its kit file so two trusts coexist on one host.
KIT_FILE := .env.$(KIT)
-include $(KIT_FILE)

# Numeric suffix of the kit (Trust_1 -> 1) — drives the per-trust overlay
# network and compose project name, preserving the deploy_trust-network-N scheme.
TRUST_NUM := $(subst Trust_,,$(KIT))
TRUST_PROJECT := trust$(TRUST_NUM)
# Dev only: expose this trust's APIs on the host. The override binds a single
# fixed host-port set, so only one dev trust can claim it — Trust_1 by
# convention, matching the old up-trust-1.
TRUST_OVERRIDE := $(if $(filter Trust_1,$(KIT)),-f deploy/compose_trust-1_override.yml,)

TRUST_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=$(or $(BASE_IMAGES_DOWNLOAD_DIR),$(if $(filter true stag,$(PROD)),/opt/flip/data/trust-$(TRUST_NUM),./data/$(KIT))) \
	OMOP_DB_PORT=$(or $(OMOP_DB_PORT),5434) \
	PACS_UI_PORT=$(or $(PACS_UI_PORT),8042) \
	ORTHANC_STORAGE_DIR=$(or $(ORTHANC_STORAGE_DIR),$(if $(filter true stag,$(PROD)),/opt/flip/orthanc/orthanc-storage,./orthanc/orthanc-storage)) \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=$(or $(XNAT_PORT),8104) \
	DATA_ACCESS_API_PORT=${DATA_ACCESS_API_PORT} \
	TRUST_DEBUG_PORT=$(or $(TRUST_DEBUG_PORT),5682) \
	IMAGING_DEBUG_PORT=$(or $(IMAGING_DEBUG_PORT),5681) \
	DATA_ACCESS_DEBUG_PORT=$(or $(DATA_ACCESS_DEBUG_PORT),5680) \
	GRAFANA_PORT=$(or $(GRAFANA_PORT),3000) \
	LOKI_PORT=$(or $(LOKI_PORT),3100) \
	TRUST_NETWORK_NAME=deploy_trust-network-$(TRUST_NUM) \
	TRUST_NUMBER=$(TRUST_NUM) \
	TRUST_API_KEY=$(TRUST_API_KEY) \
	TRUST_INTERNAL_SERVICE_KEY=$(TRUST_INTERNAL_SERVICE_KEY) \
	FL_KIT_SLOT=$(or $(FL_KIT_SLOT),$(KIT)) \
	FL_KIT_SLOT_NUMBER=$(or $(FL_KIT_SLOT_NUMBER),$(TRUST_NUM)) \
	TRUST_NAME=$(or $(FL_KIT_SLOT),$(KIT)) \
	EXPECTED_TRUST_ID=$(EXPECTED_TRUST_ID)

# Host ports and bind paths default to the local-trust allocation in
# LOCAL_TRUST_VARS below, but — like TRUST_VARS — each can be overridden from
# the kit file trust/.env.<LOCAL_TRUST_NAME> (see trust/.env.Trust_Local.example).
# The "local" trust is the on-prem trust of a production deployment (as opposed
# to the AWS EC2 trust); dev deployments use the Trust_1/Trust_2 stacks instead.
# The defaults are picked to be non-overlapping with the Trust_1/Trust_2
# allocations so the on-prem stack never clashes with a Trust_1/Trust_2 stack
# sharing the host, and so stag/prod hosts (AWS trust EC2 or on-prem via
# `make provision-local-trust`) work even with no port entries in their kit file.
# The trust network stays hardcoded — it's a structural identity, not a
# host-tunable value.
#
# ORTHANC_STORAGE_DIR defaults to a dedicated `orthanc-storage-local` path
# rather than reusing whichever trust dir matches LOCAL_TRUST_NAME. Sharing the
# dir saves the gigabytes of seeded DICOM data, but two Orthanc containers
# cannot open the same SQLite index — the second crash-loops on `database is
# locked` and the trust network loses its `orthanc` alias, which in turn
# breaks xnat-nginx upstream resolution. To re-seed the local-trust Orthanc
# on a dev laptop, copy from the matching trust dir once:
#   cp -r trust/orthanc/orthanc-storage-2/ trust/orthanc/orthanc-storage-local/
#
# Per-trust kit file (trust/.env.<LOCAL_TRUST_NAME>) — same schema as the
# KIT-selected kit above. Dropped in by the deploy's `register-trusts`
# step or by the operator after `POST /admin/trusts`.
LOCAL_TRUST_KIT_FILE := .env.$(LOCAL_TRUST_NAME)
-include $(LOCAL_TRUST_KIT_FILE)

LOCAL_TRUST_VARS = \
	BASE_IMAGES_DOWNLOAD_DIR=$(or $(BASE_IMAGES_DOWNLOAD_DIR),./data/local/$(LOCAL_TRUST_NAME)) \
	OMOP_DB_PORT=$(or $(OMOP_DB_PORT),5438) \
	PACS_UI_PORT=$(or $(PACS_UI_PORT),8046) \
	XNAT_PORT=$(or $(XNAT_PORT),8108) \
	GRAFANA_PORT=$(or $(GRAFANA_PORT),3002) \
	LOKI_PORT=$(or $(LOKI_PORT),3102) \
	TRUST_NETWORK_NAME=${network_local} \
	TRUST_NUMBER=$(subst Trust_,,$(LOCAL_TRUST_NAME)) \
	TRUST_API_KEY=$(TRUST_API_KEY) \
	TRUST_INTERNAL_SERVICE_KEY=$(TRUST_INTERNAL_SERVICE_KEY) \
	FL_KIT_SLOT=$(or $(FL_KIT_SLOT),$(LOCAL_TRUST_NAME)) \
	FL_KIT_SLOT_NUMBER=$(or $(FL_KIT_SLOT_NUMBER),$(subst Trust_,,$(LOCAL_TRUST_NAME))) \
	TRUST_NAME=$(or $(FL_KIT_SLOT),$(LOCAL_TRUST_NAME)) \
	EXPECTED_TRUST_ID=$(EXPECTED_TRUST_ID) \
	OMOP_DATA_DIR=$(or $(OMOP_DATA_DIR),./omop-db/volumes/$(LOCAL_TRUST_NAME)/db_data) \
	ORTHANC_STORAGE_DIR=$(or $(ORTHANC_STORAGE_DIR),./orthanc/orthanc-storage-local) \
	OBSERVABILITY_CONFIG_DIR=./observability \
	LOKI_DATA_VOLUME=trust-local-loki-data \
	GRAFANA_DATA_VOLUME=trust-local-grafana-data

# Guard: refuse to bring a trust up if its OMOP DB data dir is empty or missing.
# Docker would otherwise silently auto-create an empty bind mount on first start,
# and the omop-db image's init scripts (which create the schema + read-only
# data_analyst_reader role) only run when the data dir is empty AT THE POSTGRES
# DATA LEVEL — a freshly-created host dir does NOT trigger init because Postgres
# treats the missing PG_VERSION file as "broken", not "uninitialized". The
# downstream symptom is cohort queries failing with "FATAL: password
# authentication failed for user data_analyst_reader" hours later.
#
# Pass the resolved path (matching what the compose file will mount) as $1.
define check_omop_data_dir
	@dir="$(1)"; \
	if [ ! -d "$$dir" ]; then \
	    populated=0; \
	else \
	    links=$$(stat -c '%h' "$$dir" 2>/dev/null || echo 2); \
	    if [ "$$links" -gt 2 ]; then populated=1; else populated=0; fi; \
	fi; \
	if [ "$$populated" -eq 0 ]; then \
	    echo "❌ OMOP DB data dir is empty or missing: $$dir"; \
	    echo "   The omop-db container would start with no schema and no"; \
	    echo "   data_analyst_reader role — cohort queries would fail at runtime."; \
	    echo ""; \
	    echo "   Populate it first:"; \
	    echo "       make -C trust update-omop-data"; \
	    echo ""; \
	    echo "   Or, for a joining trust whose friendly name doesn't match a"; \
	    echo "   canonical data dir, point OMOP_DATA_DIR at an existing populated"; \
	    echo "   path (e.g. OMOP_DATA_DIR=./omop-db/volumes/Trust_2/db_data)."; \
	    exit 1; \
	fi
endef

# Guard: any KIT-selected target needs KIT set (it derives the project name,
# network and kit-file path).
define require_kit
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi
endef

# Guard: bringing a trust UP additionally needs its kit file (ports + credentials).
# Teardown (down-trust) only needs the project name, so it uses require_kit instead.
define require_kit_file
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi; \
	if [ ! -f "$(KIT_FILE)" ]; then \
	    echo "❌ Kit file trust/$(KIT_FILE) not found."; \
	    echo "   Copy trust/$(KIT_FILE).example to trust/$(KIT_FILE), then run"; \
	    echo "   'make register-trusts' (from the repo root) to fill in the credentials."; \
	    exit 1; \
	fi
endef

build:
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} -p $(or $(TRUST_PROJECT),trust) build

# Bring up the trust stack selected by KIT (e.g. KIT=Trust_1). Reads
# trust/.env.<KIT> for that trust's ports / dirs / credentials.
up-trust: update-omop-data update-orthanc-data create-networks
	$(require_kit_file)
	@echo "🚢 Starting Trust services (KIT=$(KIT))..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(call check_omop_data_dir,$(or $(OMOP_DATA_DIR),./omop-db/volumes/$(KIT)/db_data))
	mkdir -p $(or $(BASE_IMAGES_DOWNLOAD_DIR),./data/$(KIT))
	$(TRUST_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) up -d $(PULL_ALWAYS_FLAG)
	@echo "🩻 Starting XNAT for $(KIT)..."
	$(MAKE) -C xnat up-xnat KIT=$(KIT) PROD=$(PROD)

down-trust:
	$(require_kit)
	@echo "🛑 Stopping Trust services (KIT=$(KIT))..."
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} $(TRUST_OVERRIDE) -p $(TRUST_PROJECT) down --remove-orphans
	@echo "🩻 Stopping XNAT for $(KIT)..."
	$(MAKE) -C xnat down-xnat KIT=$(KIT) PROD=$(PROD)

restart-trust: down-trust up-trust

# EC2 variant — no host-port override, --pull always. Networks and OMOP data
# are provisioned on the instance by deploy/providers/AWS/site.yml, so the
# update-omop-data / create-networks prerequisites are intentionally omitted.
up-trust-ec2:
	$(require_kit_file)
	@echo "🚢 Starting Trust services on EC2 (KIT=$(KIT))..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(TRUST_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -p $(TRUST_PROJECT) up -d --pull always
	@echo "🩻 Starting XNAT for $(KIT)..."
	$(MAKE) -C xnat up-xnat KIT=$(KIT) PROD=$(PROD)

down-trust-ec2:
	$(require_kit)
	@echo "🛑 Stopping Trust services on EC2 (KIT=$(KIT))..."
	$(TRUST_VARS) ${DOCKER_COMPOSE_CMD} -p $(TRUST_PROJECT) down --remove-orphans
	@echo "🩻 Stopping XNAT for $(KIT)..."
	$(MAKE) -C xnat down-xnat KIT=$(KIT) PROD=$(PROD)

# LOCAL_TRUST_NAME identifies the on-prem (local) trust of a production
# deployment — it selects the kit file trust/.env.$(LOCAL_TRUST_NAME) and the
# trust-local data dirs. It is defined in deploy/providers/AWS/Makefile and
# threaded through the root Makefile. The FL participant kit the fl-client
# mounts is a separate value, FL_KIT_SLOT, carried in the kit file. The
# local-trust stack uses an isolated port set and the trust-local
# project/network so it never clashes with a Trust_1/Trust_2 stack sharing the
# host.
up-local-trust: create-networks-core create-networks-local-trust
	## Starts the on-prem (local) trust compose stack with LOCAL_TRUST_NAME=$(LOCAL_TRUST_NAME).
	## Uses project name 'trust-local' so it never clashes with a Trust_1/Trust_2 stack.
	@echo "🚢 Starting local Trust services (LOCAL_TRUST_NAME=$(LOCAL_TRUST_NAME))..."
	@echo "⚙️ Using environment file ${MAIN_ENV_FILE}"
	@echo "🧠 FL_BACKEND=$(FL_BACKEND) ($(FL_BACKEND_COMPOSE_FILE))"
	$(call check_omop_data_dir,$(or $(OMOP_DATA_DIR),./omop-db/volumes/$(LOCAL_TRUST_NAME)/db_data))
	mkdir -p $(or $(BASE_IMAGES_DOWNLOAD_DIR),./data/local/$(LOCAL_TRUST_NAME))
	$(LOCAL_TRUST_VARS) DEBUG=$(DEBUG) \
		$(DOCKER_COMPOSE_CMD) -f deploy/compose_trust.local.yml -p $(trust_local) up -d --pull always
	@echo "🩻 Starting XNAT for the local trust..."
	$(MAKE) -C xnat up-xnat-local PROD=$(PROD)

down-local-trust:
	@echo "🛑 Stopping local Trust services..."
	$(LOCAL_TRUST_VARS) \
		$(DOCKER_COMPOSE_CMD) -f deploy/compose_trust.local.yml -p $(trust_local) down --remove-orphans
	@echo "🩻 Stopping XNAT for the local trust..."
	$(MAKE) -C xnat down-xnat-local PROD=$(PROD)

# Dev convenience: bring up both standard trust stacks. Each is a separate
# sub-make so its `-include trust/.env.<KIT>` loads only that trust's kit.
up: create-networks
	$(MAKE) up-trust KIT=Trust_1
	$(MAKE) up-trust KIT=Trust_2

down:
	$(MAKE) down-trust KIT=Trust_1
	$(MAKE) down-trust KIT=Trust_2

# Start / stop only the FL client containers, leaving the rest of each trust stack up.
# Each dev trust project (trust1, trust2) runs a client per FL net, hence both net-1 and net-2.
up-fl-clients:
	@echo "🚢 Starting FL clients..."
	$(TRUST_1_VARS) DEBUG=${DEBUG} ${DOCKER_COMPOSE_CMD} -f deploy/compose_trust-1_override.yml -p ${trust1} up -d --no-deps $(PULL_ALWAYS_FLAG) fl-client-net-1 fl-client-net-2
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} up -d --no-deps $(PULL_ALWAYS_FLAG) fl-client-net-1 fl-client-net-2

down-fl-clients:
	@echo "🛑 Stopping and removing FL clients..."
	$(TRUST_1_VARS) ${DOCKER_COMPOSE_CMD} -f deploy/compose_trust-1_override.yml -p ${trust1} rm -fs fl-client-net-1 fl-client-net-2
	$(TRUST_2_VARS) ${DOCKER_COMPOSE_CMD} -p ${trust2} rm -fs fl-client-net-1 fl-client-net-2

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
# deploy_trust-network-1 when a Trust_1 stack is also running on the host. Not
# wired into the top-level `create-networks` target because `make up` doesn't
# need it; `up-local-trust` requires it explicitly.
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

# Debug targets operate on the Trust_1 stack. They recurse so the sub-make's
# `-include trust/.env.Trust_1` loads that kit (the -include is parse-time, so
# KIT must be set at make-invocation, not as a target-local variable).
debug:
	$(MAKE) DEBUG=true restart-trust KIT=Trust_1
debug-off:
	$(MAKE) DEBUG=false restart-trust KIT=Trust_1
debug-data-access-api:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=true SVC=data-access-api
debug-imaging-api:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=true SVC=imaging-api
debug-trust-api:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=true SVC=trust-api
debug-data-access-api-off:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=false SVC=data-access-api
debug-imaging-api-off:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=false SVC=imaging-api
debug-trust-api-off:
	$(MAKE) _debug-service KIT=Trust_1 DEBUG=false SVC=trust-api

# Internal helper — brings one service up under the debug compose override.
_debug-service:
	DEBUG=$(DEBUG) $(TRUST_VARS) ${DEBUG_OVERRIDE_COMPOSE_COMMAND} -p $(TRUST_PROJECT) up --remove-orphans -d $(SVC)


# Trust-side integration tests (issue #369, B3). Each delegated target brings up
# the shared trust/deploy/compose.test.yml stack via Testcontainers — run them serially
# so the per-suite session fixtures don't fight over Docker resources.
integration_test:
	$(MAKE) -C data-access-api integration_test
	$(MAKE) -C trust-api integration_test
