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
# NVFLARE network provisioning — generates the startup kits signed by the net's
# root CA. Colocated with the project YAMLs (net-*_project_*.yml), the scripts/
# they drive, and the gitignored workspace/ output. Invoke directly, e.g.
# `make -C deploy/providers/nvflare provision-2-nets`; fl-services/nvflare/Makefile
# forwards `provision`/`provision-2-nets` here.
#
# The scripts must run from the repo root: provision-network.sh uses repo-root-
# relative paths and `uv run --project fl-services/nvflare/fl-api-base`. Each recipe
# `cd`s to REPO_ROOT first so those paths resolve regardless of where make is invoked.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../../..)

# Load the env file selected by PROD exactly as the root Makefile does, so
# upload-kits-to-s3 sees AICENTRE_BUCKET_NAME / FLARE_KIT_DATE from the matching
# .env and provision-additional-client sees FL_PORT from .env.development. The
# provision targets themselves need nothing from the env, but loading it is cheap
# and keeps behaviour identical to the old root-Makefile targets.
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

# Paths are relative to the repo root (recipes cd there before running the scripts).
SCRIPTS  := deploy/providers/nvflare/scripts
PROJECTS := deploy/providers/nvflare

NET_NUMBER       ?= 1
FL_WORKSPACE_DIR ?= deploy/providers/nvflare/workspace

.PHONY: provision provision-2-nets provision-additional-client provision-stag provision-prod upload-kits-to-s3

# Dev: provision net-<NET_NUMBER> startup kits into FL_WORKSPACE_DIR (gitignored),
# where the dev compose mounts expect them.
provision:
	cd $(REPO_ROOT) && $(SCRIPTS)/provision-network.sh $(PROJECTS)/net-$(NET_NUMBER)_project_dev.yml $(NET_NUMBER) $(FL_WORKSPACE_DIR)

provision-2-nets:
	$(MAKE) provision NET_NUMBER=1
	$(MAKE) provision NET_NUMBER=2

provision-additional-client:
	cd $(REPO_ROOT) && $(SCRIPTS)/provision-additional-client.sh $(NET_NUMBER) $(FL_PORT) $(PROJECTS)/net-$(NET_NUMBER)_project_dev.yml $(FL_WORKSPACE_DIR)

# Provision the stag/prod FL network into a gitignored workspace-<env>/, ready to
# upload to S3 (see upload-kits-to-s3). Unlike dev (2 fixed clients), stag/prod
# over-provision spare client kit slots (FLIP#626): the committed
# net-<NET_NUMBER>_project_<env>.yml holds a single client template, which
# generate-project-yaml.sh clones into Trust_1 .. Trust_<N> before provisioning.
# N defaults to 50 (stag) / 500 (prod); override on the command line, e.g.
# `make -C deploy/providers/nvflare provision-prod PROD_NUM_CLIENTS=512`. The
# generated project YAML lands beside the provisioned output, both in workspace-<env>/.
STAG_NUM_CLIENTS ?= 50
PROD_NUM_CLIENTS ?= 500

provision-stag:
	cd $(REPO_ROOT) && $(SCRIPTS)/generate-project-yaml.sh $(PROJECTS)/net-$(NET_NUMBER)_project_stag.yml $(STAG_NUM_CLIENTS) workspace-stag/net-$(NET_NUMBER)_project.generated.yml
	cd $(REPO_ROOT) && $(SCRIPTS)/provision-network.sh workspace-stag/net-$(NET_NUMBER)_project.generated.yml $(NET_NUMBER) workspace-stag

provision-prod:
	cd $(REPO_ROOT) && $(SCRIPTS)/generate-project-yaml.sh $(PROJECTS)/net-$(NET_NUMBER)_project_prod.yml $(PROD_NUM_CLIENTS) workspace-prod/net-$(NET_NUMBER)_project.generated.yml
	cd $(REPO_ROOT) && $(SCRIPTS)/provision-network.sh workspace-prod/net-$(NET_NUMBER)_project.generated.yml $(NET_NUMBER) workspace-prod

# Upload the provisioned FLARE participant kits to S3 under the FLARE_KIT_DATE prefix
# the deploy flow pulls from (deploy/providers/AWS Makefile `provision-local-trust`,
# which reads s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/
# net-N/services/<slot>/). Run with PROD=stag|true so AICENTRE_BUCKET_NAME + FLARE_KIT_DATE
# load from the matching .env. Defaults to a dry run — pass DRYRUN= to upload for real.
FL_KIT_WORKSPACE := $(if $(filter true,$(PROD)),workspace-prod,workspace-stag)
DRYRUN ?= --dryrun
upload-kits-to-s3:
	@[ -n "$(AICENTRE_BUCKET_NAME)" ] || { echo "❌ AICENTRE_BUCKET_NAME not set — run with PROD=stag|true"; exit 1; }
	@[ -n "$(FLARE_KIT_DATE)" ] || { echo "❌ FLARE_KIT_DATE not set — run with PROD=stag|true"; exit 1; }
	@echo "⬆️  Uploading FLARE kits (net-$(NET_NUMBER), $(FL_KIT_WORKSPACE)) → s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/net-$(NET_NUMBER) $(DRYRUN)"
	cd $(REPO_ROOT) && aws s3 sync ./$(FL_KIT_WORKSPACE)/net-$(NET_NUMBER) s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FLARE_KIT_DATE)/net-$(NET_NUMBER) --delete $(DRYRUN)
