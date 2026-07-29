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

# Build-pipeline config (gitignored; cp .env.build.example .env.build). Included
# after the kit example so build values win inside the build pipeline.
BUILD_ENV_FILE=.env.build
ifneq ("$(wildcard $(BUILD_ENV_FILE))","")
include $(BUILD_ENV_FILE)
export $(shell sed 's/=.*//' $(BUILD_ENV_FILE))
endif

network1 = deploy_trust-network-1

service_name = omop-db
docker_command = docker compose --project-directory .. -f ../deploy/compose_trust.development.yml

lint_command = uv run ruff check . --fix
mypy_command = uv run mypy src/ tests/ --ignore-missing-imports
test_coverage_command = uv run pytest tests/unit --tb=short --disable-warnings --cov=src/omop_db_tools --cov-report=html:htmlcov-unit --cov-report=term-missing --cov-report=xml:coverage-unit.xml

# ─────────────────────────────────────────────────────────────────────────────
# Runtime-stack helpers (the omop-db service inside a trust stack)
# ─────────────────────────────────────────────────────────────────────────────

up-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR} \
	OMOP_DB_PORT=${OMOP_DB_PORT} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=Trust_1 \
	$(docker_command) up $(service_name) --pull always

down-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR} \
	OMOP_DB_PORT=${OMOP_DB_PORT} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=Trust_1 \
	$(docker_command) down --remove-orphans $(service_name) -v

shell:
	docker exec -it $$(docker ps -qf "name=$(service_name)") /bin/bash

# Sync mock omop data (used in development / staging environments where real data is not available)
# Fetched anonymously from the public Hugging Face dataset (no AWS CLI / credentials needed)
# Pass TRUST=1 or TRUST=2 to update only a single trust; defaults to all.
update-omop-data:
	TRUST=$(TRUST) CLEAN_AFTER_UPDATE=True ./update_omop_data.sh

# ─────────────────────────────────────────────────────────────────────────────
# Image build + populate pipeline (standalone; see README "Building the image")
# ─────────────────────────────────────────────────────────────────────────────

build_compose = docker compose -f compose.yml
GHCR_IMAGE = ghcr.io/londonaicentre/omop-db
OMOP_DB_TAG ?= latest
# Where fetch-vocab-core extracts the licensed vocab bundle from — a published
# tag, deliberately decoupled from OMOP_DB_TAG (which may not exist yet when
# building a new tag).
VOCAB_SOURCE_TAG ?= latest

# Fetched inputs (all gitignored under data/). The core vocab bundle contains
# licensed OMOP vocabularies (SNOMED CT, LOINC, Read, ...) and must never be
# committed; the DICOM bundle and the canonical CSV dataset are public.
# NOTE the Dockerfile's `COPY ./data/vocab_aicentre_core_20240916` must match
# VOCAB_CORE_BUNDLE when bumping the bundle version.
VOCAB_CORE_BUNDLE = vocab_aicentre_core_20240916
VOCAB_DICOM_BUNDLE = vocab_dicom_paulnagy_20260109
OMOP_CSV_DATA_VERSION = 20260729
HF_TRUST_DATA_BASE = https://huggingface.co/datasets/aicentreflip/trust-data/resolve/main

# The shipped build stack is two-trust: compose.yml defines omop-db-trust1/2 and
# .env.build carries their ports. For NUM_TRUSTS>2, add an omop-db-trust<N>
# service and an OMOP_DB_PORT_TRUST_<N> — populate-trust-<N> fails fast otherwise.
NUM_TRUSTS = 2
PARTITION = legacy

check-build-env:
	@if [ ! -f "$(BUILD_ENV_FILE)" ]; then \
		echo "❌ $(BUILD_ENV_FILE) not found — run: cp .env.build.example .env.build"; \
		exit 1; \
	fi

build: fetch-vocab-core
	$(build_compose) build

up-build: check-build-env build
	$(build_compose) up -d omop-db-trust1 omop-db-trust2

down-build:
	$(build_compose) down --remove-orphans

# Default source for the core vocab is the already-published image — it avoids
# any credentialed download and adds no new exposure (the image is public).
# Copies into a temp dir and renames on success, so a failed/interrupted copy
# is never mistaken for a present bundle on the next run.
fetch-vocab-core:
	@if [ -d "data/$(VOCAB_CORE_BUNDLE)" ]; then \
		echo "data/$(VOCAB_CORE_BUNDLE) already present — skipping."; \
	else \
		echo "⬇️  Extracting core vocab from $(GHCR_IMAGE):$(VOCAB_SOURCE_TAG) ..."; \
		mkdir -p data; \
		rm -rf "data/.$(VOCAB_CORE_BUNDLE).tmp"; \
		cid=$$(docker create $(GHCR_IMAGE):$(VOCAB_SOURCE_TAG)) || exit 1; \
		if ! docker cp "$$cid:/flip/omop/vocab" "data/.$(VOCAB_CORE_BUNDLE).tmp"; then \
			docker rm "$$cid" > /dev/null; \
			rm -rf "data/.$(VOCAB_CORE_BUNDLE).tmp"; \
			echo "❌ docker cp failed — no vocab bundle extracted"; \
			exit 1; \
		fi; \
		docker rm "$$cid" > /dev/null; \
		mv "data/.$(VOCAB_CORE_BUNDLE).tmp" "data/$(VOCAB_CORE_BUNDLE)"; \
	fi

# The DICOM vocab (NEMA PS3, via DICOM2OMOP — Apache 2.0) is freely
# redistributable and fetched anonymously from the public HF dataset.
# Downloaded to a temp file and renamed on success, so a partial download is
# never mistaken for a present bundle on the next run.
fetch-vocab-dicom:
	@if [ -d "data/$(VOCAB_DICOM_BUNDLE)" ] || [ -f "data/$(VOCAB_DICOM_BUNDLE).zip" ]; then \
		echo "data/$(VOCAB_DICOM_BUNDLE) already present — skipping."; \
	else \
		mkdir -p data; \
		if ! curl -fSL -o "data/$(VOCAB_DICOM_BUNDLE).zip.tmp" "$(HF_TRUST_DATA_BASE)/omop-vocab/$(VOCAB_DICOM_BUNDLE).zip"; then \
			rm -f "data/$(VOCAB_DICOM_BUNDLE).zip.tmp"; \
			echo "❌ download failed — no DICOM vocab bundle fetched"; \
			exit 1; \
		fi; \
		mv "data/$(VOCAB_DICOM_BUNDLE).zip.tmp" "data/$(VOCAB_DICOM_BUNDLE).zip"; \
	fi

fetch-dataset:
	uv run python -m omop_db_tools.dataset fetch --version $(OMOP_CSV_DATA_VERSION) --dest data/canonical

# Populate every build database with its slice of the canonical dataset, then
# `make apply-constraints`. Constraints are deliberately NOT part of image init:
# loading data after applying them fails.
populate: fetch-vocab-dicom fetch-dataset $(addprefix populate-trust-,$(shell seq 1 $(NUM_TRUSTS)))

populate-trust-%: check-build-env
	@if [ -z "$(OMOP_DB_PORT_TRUST_$*)" ]; then \
		echo "❌ OMOP_DB_PORT_TRUST_$* is not set — add it to $(BUILD_ENV_FILE) (and an omop-db-trust$* service to compose.yml)"; \
		exit 1; \
	fi
	./wait-for-postgres.sh $(OMOP_DB_PORT_TRUST_$*)
	OMOP_DB_PORT=$(OMOP_DB_PORT_TRUST_$*) uv run python -m omop_db_tools.load_dicom_vocab \
		--vocab-dir data/$(VOCAB_DICOM_BUNDLE)
	OMOP_DB_PORT=$(OMOP_DB_PORT_TRUST_$*) uv run python -m omop_db_tools.import_tables \
		--trust-index $* --num-trusts $(NUM_TRUSTS) --partition $(PARTITION) --data-dir data/canonical

apply-constraints: check-build-env $(addprefix apply-constraints-trust-,$(shell seq 1 $(NUM_TRUSTS)))

apply-constraints-trust-%:
	@echo "🔗 Applying OMOP CDM constraints (trust $*)..."
	$(build_compose) exec -T omop-db-trust$* psql -v ON_ERROR_STOP=1 -U $(OMOP_POSTGRES_USER) -d $(OMOP_POSTGRES_DB) -f /flip/omop/constraints.sql

# Push the already-built image to GHCR. Do `docker login ghcr.io` first if
# needed. Interactive confirmation: the trust stacks resolve :latest by default,
# so a push to the default tag repoints every default-configured deployment.
push:
	@printf "About to push %s — continue? [y/N] " "$(GHCR_IMAGE):$(OMOP_DB_TAG)"; \
	read ans; \
	if [ "$$ans" != "y" ] && [ "$$ans" != "Y" ]; then echo "aborted"; exit 1; fi
	docker push $(GHCR_IMAGE):$(OMOP_DB_TAG)

# ─────────────────────────────────────────────────────────────────────────────
# Lint & tests (the populate tooling has no service container — all local uv)
# ─────────────────────────────────────────────────────────────────────────────

lint: local_lint
mypy: local_mypy
local_lint:
	$(lint_command)
local_mypy:
	$(mypy_command)
local_test:
	$(lint_command) && $(mypy_command) && $(test_coverage_command)
unit_test: local_test
test: local_test
