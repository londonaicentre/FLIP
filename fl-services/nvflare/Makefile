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
# NVFLARE FL service targets — build, provision, and standalone run (FL services
# only, no central hub / trusts). The root Makefile forwards build-fl here by
# FL_BACKEND; the provision/up/down/submit targets are invoked here directly
# (make -C fl-services/nvflare <target>), which is where NVFLARE FL deployments
# are tested. This is the NVFLARE half of the per-backend split (Flower's
# equivalent lives in fl-services/flower/Makefile).

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# Load the FLIP dev env (FL_API_PORT, LOG_LEVEL, DEBUG, NET_ENDPOINTS, …) and the
# FL-backend var block exactly as the root Makefile does, so compose sees them.
ifneq ("$(wildcard $(REPO_ROOT)/.env.development)","")
include $(REPO_ROOT)/.env.development
export $(shell sed 's/=.*//' $(REPO_ROOT)/.env.development)
endif
include $(REPO_ROOT)/deploy/fl_backend.mk

DEV_COMPOSE := docker compose -f $(REPO_ROOT)/fl-services/nvflare/compose.dev.yml
LOCAL_DEV ?= false
NET_NUMBER ?= 1

.PHONY: build provision provision-2-nets up down submit

# Build the NVFLARE :dev images. fl-base must build first, in its own invocation:
# a plain `FROM flare-fl-base` in the derived Dockerfiles is invisible to Compose's
# build graph, so a single `compose build` would build them against a stale base.
build:
	@echo "🛠️  Building flare-fl-base:dev (LOCAL_DEV=$(LOCAL_DEV))..."
	LOCAL_DEV=$(LOCAL_DEV) $(DEV_COMPOSE) --profile build-only build fl-base
	@echo "🛠️  Building flare-fl-{server,api,client}:dev..."
	$(DEV_COMPOSE) build fl-server flip-fl-api fl-client-1

# Provision the gitignored startup kits (delegates to the root impl, which owns the
# project YAMLs + provision-network.sh). Output → deploy/providers/nvflare/workspace.
provision:
	@$(MAKE) -C $(REPO_ROOT) nvflare-provision NET_NUMBER=$(NET_NUMBER)

provision-2-nets:
	@$(MAKE) -C $(REPO_ROOT) nvflare-provision-2-nets

# Standalone FL services for one net (fl-server + fl-api + 2 clients), no hub/trusts.
# Needs `make fl-provision` first; runs on the local :dev images.
up:
	NET_NUMBER=$(NET_NUMBER) $(DEV_COMPOSE) up --remove-orphans

down:
	$(DEV_COMPOSE) down --remove-orphans

# NVFLARE standalone job submission is intentionally NOT wired: it goes through the
# FLARE admin API (provisioned admin kit), not a simple HTTP POST like Flower's
# submit. Use the simulator harness for standalone NVFLARE jobs rather than faking
# a success here.
submit:
	@echo "⚠️  NVFLARE standalone 'submit' is not wired (admin-API based, unlike Flower's HTTP submit)."
	@echo "    Run NVFLARE jobs standalone via the simulator: make -C fl-tutorials run-tutorial TUTORIAL=<name>"
	@exit 1
