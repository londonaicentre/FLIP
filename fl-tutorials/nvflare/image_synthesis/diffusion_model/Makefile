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

APP_DIR := $(CURDIR)

# Run job.py in the flip-utils environment with the `full` ML extra — the same package set the
# flare-fl-base FL image installs (`uv sync --extra full`), so a local SimEnv run matches the deployed
# image. A bare `uv run` would resolve to the repo-root venv, which has no torch/nvflare/monai.
REPO_ROOT := $(abspath $(APP_DIR)/../../../..)
FLIP_UTILS := $(REPO_ROOT)/flip-utils
FL_TUTORIALS := $(REPO_ROOT)/fl-tutorials
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
# Comment lines must be dropped before building the export list: a leading `#` token would start a
# make comment inside the `export` directive and silently un-export every variable listed after it.
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed -e '/^[[:space:]]*\#/d' -e 's/=.*//' ./.env.app)
endif

# --- Local-run knobs ---
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=10`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=diffusion_model
# NUM_ROUNDS=10`), or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from
# config.json at submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 1
N_CLIENTS ?= 2

# --- Where debug images land (local runs only) ---
# Output dir for sample images, beside the datasets under the gitignored fl-tutorials/data/. Also half
# of the simulator check: images are written only with SAVE_DEBUG_SAMPLES true in config.json AND
# LOCAL_DEV true AND this set (plot_utils.samples_enabled). Never set on a trust, which also runs
# with LOCAL_DEV=false, so production writes nothing whatever config.json says.
DEBUG_SAMPLES_DIR ?= $(FL_TUTORIALS)/data/debug_samples/diffusion_model

# --- Which dataset a local run trains on ---
# arkplus (default): the Ark+ training splits, ~1.9k X-rays per site, each site on its own folder.
# mini: xrays_mini_300, halved between the sites. For testing code only; too small to train on.
DATASET ?= arkplus
ifeq ($(DATASET),mini)
override SITE1_IMAGES_DIR :=
override SITE1_DATAFRAME :=
override SITE2_IMAGES_DIR :=
override SITE2_DATAFRAME :=
else ifneq ($(DATASET),arkplus)
$(error DATASET must be arkplus or mini, got '$(DATASET)')
endif
site_path = $(if $(1),$(abspath $(1)))

.PHONY: run export sim clean

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py).
run: sim

# --- Export job config (no GPU needed) ---
# Builds a complete FLARE job under ./fl_job/flip_fedavg/ including meta.json,
# app/config/, and app/custom/ (bundled flip/ package + staged app_files/).
# FLIP-specific values (project_id, query) are injected via environment variables.
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

# --- SimEnv local simulation (requires GPU + data) ---
# Runs the job under the FLARE simulator. Requires a GPU and the dataset DATASET names (see above).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# SITE{1,2}_* and DEV_* paths are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
# GLOBAL_ROUNDS in app_files/config.json is a PRODUCTION setting: the FL API reads it at submit time.
# A local run's round count is NUM_ROUNDS, which this target passes as --num_rounds. Keeping the two
# separate is deliberate and tree-wide (arkplus_fine_tuning runs 3 rounds locally against 50 in
# production), but the split is invisible exactly where it bites, because LOCAL_ROUNDS in that same
# file *is* read locally — the trainer reads it directly. So raising GLOBAL_ROUNDS appears to do
# nothing while its neighbour works, and the only clue is a "num_rounds=1" that never names the key
# you edited. Say it outright whenever the two disagree.
sim:
	@echo "Running tutorial with: n_clients=$(N_CLIENTS), num_rounds=$(NUM_ROUNDS)"
	@global_rounds=$$(python3 -c "import json;print(json.load(open('app_files/config.json')).get('GLOBAL_ROUNDS',''))" 2>/dev/null); \
	if [ -n "$$global_rounds" ] && [ "$$global_rounds" != "$(NUM_ROUNDS)" ]; then \
		echo "⚠️  config.json GLOBAL_ROUNDS=$$global_rounds is a production setting and is IGNORED here."; \
		echo "    This run does $(NUM_ROUNDS) round(s). To match it locally: make run NUM_ROUNDS=$$global_rounds"; \
	fi
	@if [ "$(DATASET)" = arkplus ] && [ ! -f "$(SITE1_DATAFRAME)" ]; then \
		echo "Ark+ data not found at $(SITE1_DATAFRAME)."; \
		echo "Run: make -C $(FL_TUTORIALS) download-arkplus-finetuning-data"; \
		echo "Or, to test code only: make run DATASET=mini"; \
		exit 1; \
	fi
	SITE1_IMAGES_DIR="$(call site_path,$(SITE1_IMAGES_DIR))" \
	SITE1_DATAFRAME="$(call site_path,$(SITE1_DATAFRAME))" \
	SITE2_IMAGES_DIR="$(call site_path,$(SITE2_IMAGES_DIR))" \
	SITE2_DATAFRAME="$(call site_path,$(SITE2_DATAFRAME))" \
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	DEBUG_SAMPLES_DIR="$(abspath $(DEBUG_SAMPLES_DIR))" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

clean:
	rm -rf ./fl_job
