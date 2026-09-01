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

# Backend-aware entrypoint for the FL tutorial runners. The runner + harness live per-backend
# under fl-tutorials/<backend>/ (fl-tutorials/nvflare/ and fl-tutorials/flower/); the shared
# dataset tooling lives in fl-tutorials/datasets/ (outputs in the gitignored fl-tutorials/data/,
# one download serving both backends). This thin Makefile forwards each target to the right
# sub-make so the documented entrypoint
#   make -C fl-tutorials <target> [TUTORIAL=... NUM_CASES=...]
# keeps working and selects the backend via FL_BACKEND (default nvflare). Command-line
# variables are forwarded to the sub-make automatically.
FL_BACKEND ?= nvflare

TUTORIAL_TARGETS := list-tutorials run-tutorial run-all-tutorials
# Dataset targets are backend-agnostic (shared data root, single copy in datasets/);
# FL_BACKEND still selects a per-harness variant where one exists (the two spleen downloads).
DATASET_TARGETS := download-xray-data download-spleen-data upload-spleen-labels \
	download-arkplus-finetuning-data download-arkplus-eval-data \
	fetch-spleen-metadata-table download-spleen-msd-raw convert-spleen-to-dicom \
	create-spleen-metadata-table build-spleen-omop-tables verify-spleen-omop-tables \
	reproduce-spleen-omop

.PHONY: $(TUTORIAL_TARGETS) $(DATASET_TARGETS)
$(TUTORIAL_TARGETS):
	@$(MAKE) --no-print-directory -C $(FL_BACKEND) $@

$(DATASET_TARGETS):
	@$(MAKE) --no-print-directory -C datasets $@ FL_BACKEND=$(FL_BACKEND)

# ---------------------------------------------------------------------------
# Lint / autoformat the tutorial sources. Runs ruff over the whole tree; ruff
# auto-excludes the per-tutorial .venv/ trees and resolves config per-file
# (NVFLARE tutorials inherit the repo-root rule set, Flower tutorials use their
# own pyproject [tool.ruff]). Uses uvx so no synced venv is required.
#
# NOTE: `format` reformats every tutorial, including the Flower server_app.py /
# strategy.py / evaluation pyproject.toml files that scripts/check_tutorial_sync.sh
# requires to stay byte-identical to their fl-apps/flower/ templates. If `format`
# touches one, re-sync by copying the fl-apps/flower/ template over the tutorial
# file (see scripts/check_tutorial_sync.sh).
RUFF ?= uvx ruff@0.14.7

.PHONY: lint format
lint:
	$(RUFF) check .

format:
	$(RUFF) format .
	$(RUFF) check --fix .

# ---------------------------------------------------------------------------
# CPU-only pytest over fl-tutorials/tests/, split across two environments because the two
# halves of that tree need different dependencies.
#
# Tutorial-app tests (everything under tests/ except tests/datasets/) run in flip-utils'
# environment (`flip-utils[full]`: monai, pydicom, torch), which is what the FL images give
# these apps at runtime, rather than in a per-tutorial venv — arkplus_fine_tuning/pyproject.toml
# does not even declare monai, so that env cannot import its own data_utils.py. See
# tests/README.md.
PYTEST ?= uv run --project ../flip-utils --extra full --group dev pytest

# Dataset-tooling tests (tests/datasets/) exercise datasets/**, which is workstation tooling,
# not FL-runtime code — flip-utils has no use for pandera/sqlglot, so those tests run against
# datasets/spleen's own project instead, which already declares everything they need
# (pandera[pandas,io], sqlglot, pandas, pydicom, natsort, monai). See tests/README.md.
DATASETS_PYTEST ?= uv run --project datasets/spleen --group dev pytest

.PHONY: pytest pytest-datasets test unit_test
pytest:
	$(PYTEST) tests --ignore=tests/datasets

pytest-datasets:
	$(DATASETS_PYTEST) tests/datasets

test: lint pytest pytest-datasets

unit_test: test
