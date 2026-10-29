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
# The Snowflake (SPCS) shape of a FLIP trust node, beside helm/ and the compose files
# (FLIP#1589). Infrastructure - roles, warehouse, pools, database, stages, image
# repository - belongs to the Terraform root trust/deploy/terraform/snowflake (FLIP#1581);
# this Makefile owns the node: images, secrets, specs and the services themselves.
#
# Every target that talks to Snowflake accepts DRY_RUN=true, which prints the SQL instead
# of running it. That is not only a safety net: it is how the deploy path is tested, since
# CI has no Snowflake account and no credentials.

SNOWFLAKE_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(SNOWFLAKE_DIR)/../../..)

# Kit-file env token (ENV) from HUB, derived in one place for every FLIP Makefile that
# reads HUB (FLIP#1479: prod and stag select trust/.env.<CODE>.prod and
# .stag). Included before anything that needs the kit file's name, as the Helm
# Makefile does.
include $(REPO_ROOT)/deploy/env_mode.mk

KIT ?=
KIT_ENV := $(ENV)
KIT_FILE := $(REPO_ROOT)/trust/.env.$(KIT).$(KIT_ENV)

# The snow CLI connection to act through. Default is the shared test account; an operator
# deploying a real trust names their own. Never a credential: `snow connection add` holds
# the key pair, outside this repo.
SNOW_CONNECTION ?= flip-test
SNOW ?= snow
PYTHON ?= python3

# Where the rendered specs land. One directory per trust and environment, so two trusts
# synced from one workstation cannot overwrite each other's specs.
RENDER_DIR := $(SNOWFLAKE_DIR)rendered/$(shell echo $(KIT) | tr 'a-z' 'A-Z').$(KIT_ENV)

# Images mirrored into the account repository. GHCR is the source; the account repository
# is what SPCS can pull from, and a digest recorded at mirror time is what the specs then
# reference. linux/amd64 explicitly: SPCS runs amd64 only, and an arm64 workstation pulls
# arm64 by default, which would push an image no compute pool can start.
GHCR_ORG ?= ghcr.io/londonaicentre
MIRROR_PLATFORM := linux/amd64
# FL client image name follows the kit's backend (flare-fl-client / flower-supernode, as in
# deploy/fl_backend.mk); mirror_images.py appends it.
MIRROR_IMAGES ?= trust-api data-access-api imaging-api orthanc xnat-web xnat-db xnat-nginx xnat-dcm2niix

DRY_RUN ?=
ifeq ($(strip $(DRY_RUN)),true)
RUN_SQL = cat
SQL_MODE = (dry run - not executed)
else
# --enable-templating NONE: snow otherwise rewrites & / &{..} in specs and SQL (&& becomes &).
RUN_SQL = $(SNOW) sql --stdin --enable-templating NONE --connection $(SNOW_CONNECTION)
SQL_MODE =
endif

.PHONY: help require-kit render mirror-images sync-kit up down suspend status selftest test clean \
	xnat-configure seed-orthanc smoke-cstore reconvert-nifti job-status

help:
	@echo "FLIP trust on Snowflake (SPCS) - trust/deploy/snowflake"
	@echo ""
	@echo "  make render        KIT=<CODE> HUB=<env>   Render one SPCS spec per service"
	@echo "  make mirror-images KIT=<CODE> HUB=<env>   Mirror GHCR images into the account repository"
	@echo "                    MIRROR_PULL=false       ...pushing the local daemon's images (local builds) as is"
	@echo "  make sync-kit      KIT=<CODE> HUB=<env>   Write SECRET objects, push FL kit + governance"
	@echo "  make up            KIT=<CODE> HUB=<env>   CREATE OR ALTER SERVICE from the rendered specs"
	@echo "  make down          KIT=<CODE> HUB=<env>   Drop every service"
	@echo "  make suspend       KIT=<CODE> HUB=<env>   Suspend every service AND every compute pool"
	@echo "  make status        KIT=<CODE> HUB=<env>   Service and compute-pool status"
	@echo "  make selftest      KIT=<CODE> HUB=<env>   Placeholder self-test (real asserts: FLIP#1585)"
	@echo ""
	@echo "  One-shot jobs (EXECUTE JOB SERVICE, ASYNC; follow with job-status):"
	@echo "  make xnat-configure KIT=<CODE> HUB=<env>  Register the DICOM receiver, the PACS and the DQR schedule"
	@echo "  make seed-orthanc   KIT=<CODE> HUB=<env>  Seed the mocked PACS from the SEED stage"
	@echo "  make smoke-cstore   KIT=<CODE> HUB=<env>  Drive a real C-STORE and check the receiver"
	@echo "  make reconvert-nifti KIT=<CODE> HUB=<env> Convert archived scans that have DICOM but no NIfTI"
	@echo "  make job-status     KIT=<CODE> JOB=<name> Status and logs of one job service"
	@echo ""
	@echo "  make test                                  Spec and sync-kit guards (no account needed)"
	@echo ""
	@echo "  Add DRY_RUN=true to any Snowflake target to print the SQL instead of running it."
	@echo "  SNOW_CONNECTION=$(SNOW_CONNECTION)"

require-kit:
	@if [ -z "$(strip $(KIT))" ]; then \
		echo "❌ KIT is required - the trust CODE, e.g. make render KIT=GSTT HUB=stag"; \
		exit 1; \
	fi

# ── Specs ────────────────────────────────────────────────────────────────────
render: require-kit
	@$(PYTHON) $(SNOWFLAKE_DIR)render_specs.py --kit $(KIT) --env $(KIT_ENV) --out-dir $(RENDER_DIR)

# ── Images ───────────────────────────────────────────────────────────────────
# Pull by tag from GHCR, retag into the account repository, push, and record the digest
# the registry returned. The digest is what the rendered specs reference: a tag in the
# account repository can be moved afterwards, a digest cannot, so this is the difference
# between a reproducible deploy and one that silently changes under a re-push.
mirror-images: require-kit
	@$(PYTHON) $(SNOWFLAKE_DIR)mirror_images.py \
		--kit $(KIT) --env $(KIT_ENV) \
		--connection $(SNOW_CONNECTION) --snow $(SNOW) \
		--ghcr-org $(GHCR_ORG) --platform $(MIRROR_PLATFORM) \
		--images "$(MIRROR_IMAGES)" \
		$(if $(filter true,$(DRY_RUN)),--dry-run,) \
		$(if $(filter false,$(MIRROR_PULL)),--no-pull,)

# ── Secrets, FL kit, governance ──────────────────────────────────────────────
sync-kit: require-kit
	@$(PYTHON) $(SNOWFLAKE_DIR)sync_snowflake_kit.py \
		--kit $(KIT) --env $(KIT_ENV) \
		--connection $(SNOW_CONNECTION) --snow $(SNOW) \
		$(if $(FL_KIT_DIR),--fl-kit-dir $(FL_KIT_DIR),) \
		$(if $(filter true,$(DRY_RUN)),--dry-run,)

# ── Services ─────────────────────────────────────────────────────────────────
# Services are created AS the services role (a service's token is its owner's role), which holds
# CREATE SERVICE only for the length of this target: spcs_sql.py apply-up grants it, creates,
# and revokes in a `finally`, so a failed deploy cannot leave it behind (FLIP#1578).
up: require-kit render
	@echo "🚀 CREATE OR ALTER SERVICE for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py apply-up --dir $(RENDER_DIR) \
		--snow $(SNOW) --connection $(SNOW_CONNECTION) \
		$(if $(filter true,$(DRY_RUN)),--dry-run,)

down: require-kit
	@echo "🛑 DROP SERVICE for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py down --dir $(RENDER_DIR) | $(RUN_SQL)

# Idle pools bill every node they hold, and a resource monitor does not cover them.
suspend: require-kit
	@echo "💤 Suspending services and compute pools for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py suspend --dir $(RENDER_DIR) | $(RUN_SQL)

status: require-kit
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py status --dir $(RENDER_DIR) | $(RUN_SQL)

# ── One-shot jobs ────────────────────────────────────────────────────────────
# EXECUTE JOB SERVICE against a rendered job spec, run AS the services role with
# CREATE SERVICE granted for the length of the call and revoked in a `finally`
# (spcs_sql.py run_job), exactly as `up` does. ASYNC = TRUE throughout: a statement
# times out at 300 s and configure-xnat.sh alone outlives that on a cold site, so a
# synchronous call would report a timeout for a job that is running correctly. Each
# returns immediately; follow with `make job-status JOB=<name>`.
define run_job
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py run-job --dir $(RENDER_DIR) --service $(1) \
		--snow $(SNOW) --connection $(SNOW_CONNECTION) \
		$(if $(filter true,$(DRY_RUN)),--dry-run,)
	@$(if $(filter true,$(DRY_RUN)),true,echo "   Follow it: make job-status KIT=$(KIT) HUB=$(HUB) JOB=$(1)")
endef

# Not optional on a new deploy. Until this has run XNAT has registered no DICOM
# receiver and rejects every C-STORE, so the trust answers HTTP and retrieves nothing
# (spike FLIP#1577). The compose twin is `make -C trust/xnat xnat-configure`, the
# Kubernetes twin the chart's xnat-init hook.
xnat-configure: require-kit render
	@echo "🔧 Configuring XNAT for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	$(call run_job,xnat-configure)

# The PACS half of `make -C trust seed`. Put the DICOM on the stage first, once:
#   snow stage copy <dicom dir> @FLIP_$(KIT)... /dicom/ --recursive --enable-templating NONE
# A re-run is a no-op (Orthanc dedupes on SOPInstanceUID).
seed-orthanc: require-kit render
	@echo "🌱 Seeding the mocked PACS for $(KIT) ($(KIT_ENV)) from the SEED stage $(SQL_MODE)"
	$(call run_job,seed-orthanc)

# A real C-STORE through the mocked PACS, then the receiver's own evidence. The twin of
# `make -C trust/deploy/helm smoke-cstore`. A C-ECHO passes while every store aborts, so
# this is the only check that says the DICOM path works (FLIP#1228).
smoke-cstore: require-kit render
	@echo "🩺 C-STORE smoke for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	$(call run_job,smoke-cstore)

# `make -C trust reconvert-nifti`, on the Snowflake conversion path: imaging-api's own
# script, with CONVERTER_BACKEND=snowflake, so it CALLs APP.CONVERT_SCAN instead of
# XNAT's Container Service bulk launcher. RECONVERT_ARGS overrides the default --launch.
reconvert-nifti: require-kit render
	@echo "🔁 Reconverting scans without NIfTI for $(KIT) ($(KIT_ENV)) $(SQL_MODE)"
	$(call run_job,reconvert)

JOB ?=
job-status: require-kit
	@if [ -z "$(strip $(JOB))" ]; then \
		echo "❌ JOB is required, e.g. make job-status KIT=$(KIT) JOB=xnat-configure"; \
		exit 1; \
	fi
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py job-status --dir $(RENDER_DIR) --service $(JOB) | $(RUN_SQL)

selftest: require-kit
	@echo "🧪 Self-test for $(KIT) ($(KIT_ENV)) $(SQL_MODE) - placeholder, real asserts in FLIP#1585"
	@$(PYTHON) $(SNOWFLAKE_DIR)spcs_sql.py selftest --dir $(RENDER_DIR) | $(RUN_SQL)

# ── Guards ───────────────────────────────────────────────────────────────────
# No Snowflake account, no credentials, no network: everything is asserted over rendered
# specs and a fake `snow` binary.
test:
	@cd $(SNOWFLAKE_DIR) && uv run --no-project --with pytest --with "pyyaml==6.0.3" pytest tests/ -v

clean:
	@rm -rf $(SNOWFLAKE_DIR)rendered
	@echo "✓ Removed rendered specs"
