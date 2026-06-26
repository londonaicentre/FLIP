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
# NVFLARE FL service + network-provisioning targets — build, provision (startup
# kits), standalone run, and the S3 kit upload (FL services only, no central hub /
# trusts). Everything NVFLARE lives in this tree: the project YAMLs
# (net-*_project_*.yml), the provisioning scripts/, and the gitignored
# workspace-{dev,stag,prod}/ output all sit beside this Makefile. The root Makefile
# forwards build-fl here by FL_BACKEND; every other target is invoked directly
# (make -C fl-services/nvflare <target>). Flower's equivalent is fl-services/flower/Makefile.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# Load the env file selected by PROD (dev by default), exactly as the root Makefile
# does, so compose + the provisioning targets see the dev vars (FL_API_PORT,
# LOG_LEVEL, NET_ENDPOINTS, FL_PORT, …) and, with PROD=stag|true, the upload vars
# (AICENTRE_BUCKET_NAME, FLARE_KIT_DATE) used by upload-kits-to-s3.
ifeq ($(PROD),true)
MAIN_ENV_FILE := $(REPO_ROOT)/.env.production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE := $(REPO_ROOT)/.env.stag
else
MAIN_ENV_FILE := $(REPO_ROOT)/.env.development
endif
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif
include $(REPO_ROOT)/deploy/fl_backend.mk

DEV_COMPOSE := docker compose -f $(REPO_ROOT)/fl-services/nvflare/compose.dev.yml
LOCAL_DEV ?= false
NET_NUMBER ?= 1

# Network provisioning lives alongside this Makefile, so recipes use paths relative
# to fl-services/nvflare/ (no repo-root cd): the scripts read net-*_project_*.yml and
# write the gitignored workspace-{dev,stag,prod}/ output here, and provision-network.sh
# invokes `uv run --project fl-api-base` (the colocated uv project that declares nvflare).
SCRIPTS            := scripts
DEV_WORKSPACE_DIR  ?= workspace-dev
STAG_WORKSPACE_DIR ?= workspace-stag
PROD_WORKSPACE_DIR ?= workspace-prod
STAG_NUM_CLIENTS   ?= 50
PROD_NUM_CLIENTS   ?= 500

.PHONY: build provision provision-2-nets provision-additional-client provision-stag provision-prod upload-kits-to-s3 up down submit

# Build the NVFLARE :dev images. fl-base must build first, in its own invocation:
# a plain `FROM flare-fl-base` in the derived Dockerfiles is invisible to Compose's
# build graph, so a single `compose build` would build them against a stale base.
build:
	@echo "🛠️  Building flare-fl-base:dev (LOCAL_DEV=$(LOCAL_DEV))..."
	LOCAL_DEV=$(LOCAL_DEV) $(DEV_COMPOSE) --profile build-only build fl-base
	@echo "🛠️  Building flare-fl-{server,api,client}:dev..."
	$(DEV_COMPOSE) build fl-server flip-fl-api fl-client-1

# Dev: provision net-<NET_NUMBER> startup kits into DEV_WORKSPACE_DIR (gitignored),
# where the dev compose mounts expect them.
provision:
	$(SCRIPTS)/provision-network.sh net-$(NET_NUMBER)_project_dev.yml $(NET_NUMBER) $(DEV_WORKSPACE_DIR)

provision-2-nets:
	$(MAKE) provision NET_NUMBER=1
	$(MAKE) provision NET_NUMBER=2

# Onboard one extra client to an existing net (after adding its Trust_<N> entry to the
# project YAML) without re-provisioning — and thus re-signing — the whole network.
provision-additional-client:
	$(SCRIPTS)/provision-additional-client.sh $(NET_NUMBER) $(FL_PORT) net-$(NET_NUMBER)_project_dev.yml $(DEV_WORKSPACE_DIR)

# Stag/prod: over-provision spare client kit slots (FLIP#626). The committed
# net-<NET_NUMBER>_project_<env>.yml holds a single client template, which
# generate-project-yaml.sh clones into Trust_1 .. Trust_<N> before provisioning into
# the gitignored {STAG,PROD}_WORKSPACE_DIR. N defaults to 50 (stag) / 500 (prod);
# override e.g. `make -C fl-services/nvflare provision-prod PROD_NUM_CLIENTS=512`.
provision-stag:
	$(SCRIPTS)/generate-project-yaml.sh net-$(NET_NUMBER)_project_stag.yml $(STAG_NUM_CLIENTS) $(STAG_WORKSPACE_DIR)/net-$(NET_NUMBER)_project.generated.yml
	$(SCRIPTS)/provision-network.sh $(STAG_WORKSPACE_DIR)/net-$(NET_NUMBER)_project.generated.yml $(NET_NUMBER) $(STAG_WORKSPACE_DIR)

provision-prod:
	$(SCRIPTS)/generate-project-yaml.sh net-$(NET_NUMBER)_project_prod.yml $(PROD_NUM_CLIENTS) $(PROD_WORKSPACE_DIR)/net-$(NET_NUMBER)_project.generated.yml
	$(SCRIPTS)/provision-network.sh $(PROD_WORKSPACE_DIR)/net-$(NET_NUMBER)_project.generated.yml $(NET_NUMBER) $(PROD_WORKSPACE_DIR)

# Upload the provisioned FLARE participant kits to S3 under the FLARE_KIT_DATE prefix
# the deploy flow pulls from (deploy/providers/AWS Makefile `provision-local-trust`,
# which reads s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/
# net-N/services/<slot>/). Run with PROD=stag|true so AICENTRE_BUCKET_NAME + FLARE_KIT_DATE
# load from the matching .env. Defaults to a dry run — pass DRYRUN= to upload for real.
FL_KIT_WORKSPACE := $(if $(filter true,$(PROD)),$(PROD_WORKSPACE_DIR),$(STAG_WORKSPACE_DIR))
DRYRUN ?= --dryrun
upload-kits-to-s3:
	@[ -n "$(AICENTRE_BUCKET_NAME)" ] || { echo "❌ AICENTRE_BUCKET_NAME not set — run with PROD=stag|true"; exit 1; }
	@[ -n "$(FLARE_KIT_DATE)" ] || { echo "❌ FLARE_KIT_DATE not set — run with PROD=stag|true"; exit 1; }
	@echo "⬆️  Uploading FLARE kits (net-$(NET_NUMBER), $(FL_KIT_WORKSPACE)) → s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/net-$(NET_NUMBER) $(DRYRUN)"
	aws s3 sync ./$(FL_KIT_WORKSPACE)/net-$(NET_NUMBER) s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/net-$(NET_NUMBER) --delete $(DRYRUN)

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
