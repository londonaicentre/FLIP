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

# Backend-aware entrypoint for the FL tutorial runners. The actual runner + harness live
# per-backend under fl-tutorials/<backend>/ (fl-tutorials/nvflare/ and fl-tutorials/flower/). This thin
# Makefile forwards every target to fl-tutorials/$(FL_BACKEND)/ so the documented entrypoint
#   make -C fl-tutorials <target> [TUTORIAL=... NUM_CASES=...]
# keeps working and selects the backend via FL_BACKEND (default nvflare). Command-line
# variables are forwarded to the sub-make automatically.
FL_BACKEND ?= nvflare

TUTORIAL_TARGETS := list-tutorials run-tutorial run-all-tutorials \
	download-xray-data download-spleen-data \
	download-arkplus-finetuning-data download-arkplus-eval-data

.PHONY: $(TUTORIAL_TARGETS)
$(TUTORIAL_TARGETS):
	@$(MAKE) --no-print-directory -C $(FL_BACKEND) $@

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
