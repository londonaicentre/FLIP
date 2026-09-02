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

# Pull defaults from the per-trust kit example (the only tracked env source —
# the real kit file is gitignored and may not exist on a dev box).
KIT_ENV_FILE=../.env.GSTT.development.example
ifneq ("$(wildcard $(KIT_ENV_FILE))","")
include $(KIT_ENV_FILE)
export $(shell sed 's/=.*//' $(KIT_ENV_FILE))
endif

service_name = orthanc
docker_command = docker compose --project-directory .. -f ../deploy/compose_trust.development.yml
run_clean = run --rm --remove-orphans

network1 = deploy_trust-network-1

TRUST_1_VARS = \
	PACS_UI_PORT=${PACS_UI_PORT} \
	ORTHANC_STORAGE_DIR=${ORTHANC_STORAGE_DIR} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT} \
	TRUST_NETWORK_NAME=${network1}

build:
	$(docker_command) build $(service_name)
up-trust-1:
	$(TRUST_1_VARS) $(docker_command) up $(service_name)
down:
	$(TRUST_1_VARS) $(docker_command) down --remove-orphans $(service_name)
shell:
	$(TRUST_1_VARS) $(docker_command) $(run_clean) $(service_name) /bin/bash

# Sync mock orthanc DICOM data (used in development / staging environments where real data is not available)
# Fetched anonymously from the public Hugging Face dataset (no AWS CLI / credentials needed)
# Pass TRUST=1 or TRUST=2 to update only a single trust; defaults to all.
update-orthanc-data:
	TRUST=$(TRUST) \
	CLEAN_AFTER_UPDATE=True \
	ORTHANC_STORAGE_DIR_TRUST_1=$(ORTHANC_STORAGE_DIR_TRUST_1) \
	ORTHANC_STORAGE_DIR_TRUST_2=$(ORTHANC_STORAGE_DIR_TRUST_2) \
	./update_orthanc_data.sh

# Seed a RUNNING trust Orthanc with this trust's slice of each listed project's
# DICOMs (FLIP#1100) — the PACS twin of `make -C omop-db seed-omop`. Both select
# the trust's studies by the same `source_trust` column of the published OMOP
# tables, so the studies landing here are exactly the ones the trust's
# image_occurrence rows point at. Streams dicom/<project>.tar.gz at the pinned
# data version (a tag on the HF dataset; HF_TRUST_DATA_REVISION overrides) into
# volumes/dicom/<revision>/ once (cached), then POSTs /instances; Orthanc dedupes on
# SOPInstanceUID, so a re-run is a no-op. CLEAR=1 deletes these projects'
# existing studies first (e.g. to replace unpatched instances — Orthanc never
# overwrites an AlreadyStored one). Normally via `make -C trust seed-orthanc
# KIT=<CODE>`, which supplies the slot, port and credentials from the kit:
#   make seed-orthanc TRUST_INDEX=2 PACS_UI_PORT=8044 PROJECTS="spleen_project"
# Writes <storage dir>/.seeded (inside — the two default storage dirs share a
# parent) so update_orthanc_data.sh refuses to re-snapshot over it without FORCE=1.
TRUST_INDEX ?= 1
PROJECTS ?= cxr_project spleen_project
SEED_STORAGE_DIR = $(or $(ORTHANC_STORAGE_DIR_TRUST_$(TRUST_INDEX)),$(ORTHANC_STORAGE_DIR))
seed-orthanc:
	PACS_UI_PORT=$(PACS_UI_PORT) ORTHANC_USERNAME=$(ORTHANC_USERNAME) ORTHANC_PASSWORD='$(ORTHANC_PASSWORD)' \
	  uv run seed_orthanc.py --trust-index $(TRUST_INDEX) --projects $(PROJECTS) \
	  $(if $(filter 1,$(CLEAR)),--clear-projects) $(if $(filter 1,$(DRY_RUN)),--dry-run)
	@$(if $(filter 1,$(DRY_RUN)),true,printf 'projects=%s\nversion=%s\n' "$(PROJECTS)" "$${HF_TRUST_DATA_REVISION:-$$(tr -d ' \n\r\t' < ../.data_version)}" > "$(SEED_STORAGE_DIR)/.seeded" && echo "✅ marker: $(SEED_STORAGE_DIR)/.seeded")

# seed_orthanc.py's unit tests (pure selection/guard logic + the upload contract
# against a scripted session) and publish_dicom.py's (the both-ways verification).
# PEP 723 scripts, no project: pytest via uv.
test:
	uv run --no-project --with pytest --with 'requests>=2.32.5' --with 'pydicom>=3.0.1' pytest tests -q
	uvx ruff@0.14.7 check . && uvx ruff@0.14.7 format --check .
