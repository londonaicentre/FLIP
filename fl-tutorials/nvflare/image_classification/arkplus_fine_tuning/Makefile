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
# image. A bare `uv run` would resolve to the repo-root venv, which has no torch/nvflare/monai. The
# checkpoint-preprocessing target instead uses the tutorial-local `uv run` (this dir's pyproject.toml:
# torch + timm, no NVFLARE), matching the legacy tutorial.
FLIP_UTILS := $(abspath $(APP_DIR)/../../../../flip-utils)
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
# Comment lines must be dropped before building the export list: a leading `#` token would start a
# make comment inside the `export` directive and silently un-export every variable listed after it.
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed -e '/^[[:space:]]*#/d' -e 's/=.*//' ./.env.app)
endif

# --- Local-run knobs ---
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=10`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=arkplus_fine_tuning NUM_ROUNDS=10`),
# or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from config.json at
# submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 3
N_CLIENTS ?= 2

# The backbone checkpoint the server loads (config.json SERVER_CHECKPOINT). Staged into app_files/ so
# stage_app_files() bundles it into the server custom dir; clients receive the backbone at round 0.
PROCESSED_CHECKPOINT := $(APP_DIR)/app_files/pretrained_weights.pt

# Resolve a (possibly empty) path against this app folder: absolute paths are kept, relative paths are
# taken relative to the app directory, and empty stays empty. This lets .env.app (or a `make VAR=...`
# override) use app-relative paths such as RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar,
# regardless of the directory make is invoked from. `override` makes the resolution apply to
# command-line values too.
abspath_app = $(if $(strip $(1)),$(abspath $(if $(filter /%,$(strip $(1))),$(strip $(1)),$(APP_DIR)/$(strip $(1)))))

override RAW_CHECKPOINT   := $(call abspath_app,$(RAW_CHECKPOINT))
override DEV_IMAGES_DIR   := $(call abspath_app,$(DEV_IMAGES_DIR))
override DEV_DATAFRAME    := $(call abspath_app,$(DEV_DATAFRAME))
override SITE1_IMAGES_DIR := $(call abspath_app,$(SITE1_IMAGES_DIR))
override SITE1_DATAFRAME  := $(call abspath_app,$(SITE1_DATAFRAME))
override SITE2_IMAGES_DIR := $(call abspath_app,$(SITE2_IMAGES_DIR))
override SITE2_DATAFRAME  := $(call abspath_app,$(SITE2_DATAFRAME))

.PHONY: run export sim download-raw-checkpoint prepare-checkpoint clean

# --- Raw checkpoint download ---
download-raw-checkpoint:
	@if [ -z "$(RAW_CHECKPOINT)" ]; then \
		echo "❌ RAW_CHECKPOINT is not set."; \
		echo "   Set it in .env.app (an app-relative path is fine, e.g."; \
		echo "   RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar)."; \
		exit 1; \
	fi
	@echo "Downloading Ark+ foundation model checkpoint..."
	@mkdir -p "$(dir $(RAW_CHECKPOINT))"
	@curl -L -o "$(RAW_CHECKPOINT)" "https://www.dropbox.com/scl/fo/joycn8m93nvlrc8yjme40/ABBtPc5oaalYZ7kzmERpjhU/Ark%2B_Nature/Ark6_swinLarge768_ep50.pth.tar?rlkey=p3lphqvtgmiphw1n0u039jpjn&dl=1"
	@echo "✅ Downloaded Ark+ foundation model checkpoint to $(RAW_CHECKPOINT)"

# --- Checkpoint preprocessing ---
# The finetuning app needs the foundation-model checkpoint as a clean backbone-only `.pt` at
# app_files/pretrained_weights.pt. InitialCheckpointPTModelPersistor loads it (strict=False) into the
# round-0 global model and broadcasts it. `prepare-checkpoint` is a no-op if it already exists.
prepare-checkpoint:
	@if [ -f "$(PROCESSED_CHECKPOINT)" ]; then \
		echo "✅ Processed Ark+ checkpoint found."; \
	elif [ -z "$(RAW_CHECKPOINT)" ]; then \
		echo "❌ RAW_CHECKPOINT is not set, and no processed checkpoint exists yet."; \
		echo ""; \
		echo "Point RAW_CHECKPOINT at the Ark+ foundation model checkpoint"; \
		echo "(Ark6_swinLarge768_ep50.pth.tar). An app-relative path is fine, e.g."; \
		echo "set RAW_CHECKPOINT=models/Ark6_swinLarge768_ep50.pth.tar in .env.app."; \
		echo ""; \
		echo "Don't have the file? Fetch it with: make download-raw-checkpoint"; \
		echo "Or request access: https://forms.gle/qkoDGXNiKRPTDdCe8"; \
		exit 1; \
	elif [ ! -f "$(RAW_CHECKPOINT)" ]; then \
		echo "❌ Raw Ark+ checkpoint not found at: $(RAW_CHECKPOINT)"; \
		echo ""; \
		echo "Fetch it automatically with: make download-raw-checkpoint"; \
		echo "Or request access to Ark6_swinLarge768_ep50.pth.tar: https://forms.gle/qkoDGXNiKRPTDdCe8"; \
		exit 1; \
	else \
		echo "Processing raw checkpoint..."; \
		uv run python "$(APP_DIR)/process_tools/preprocess_checkpoints.py" \
			--entry "$(RAW_CHECKPOINT)" "$(PROCESSED_CHECKPOINT)" arkplus_pretrained; \
	fi

# --- Export job config (no GPU needed) ---
# Builds a complete NVFLARE job under ./fl_job/flip_fedavg/ including meta.json, app/config/, and
# app/custom/ (bundled flip/ package + staged app_files/; the checkpoint is staged to the server only).
export: prepare-checkpoint
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

# --- SimEnv local simulation (requires GPU + data + checkpoint) ---
# Runs the job under the NVFLARE simulator. site-1/site-2 train on distinct data: the trainer reads
# SITE{1,2}_* by flare.get_site_name() (see app_files/data_utils.py). DEV_*/SITE{1,2}_* are already
# resolved to absolute paths above so the simulator's client workers (which run from the workspace dir,
# not here) find the LOCAL_DEV dataset. FLIP-specific values come from FLIP_PROJECT_ID / FLIP_QUERY.
sim:
	@echo "Running tutorial with: n_clients=$(N_CLIENTS), num_rounds=$(NUM_ROUNDS)"
	DEV_IMAGES_DIR="$(DEV_IMAGES_DIR)" DEV_DATAFRAME="$(DEV_DATAFRAME)" \
	SITE1_IMAGES_DIR="$(SITE1_IMAGES_DIR)" SITE1_DATAFRAME="$(SITE1_DATAFRAME)" \
	SITE2_IMAGES_DIR="$(SITE2_IMAGES_DIR)" SITE2_DATAFRAME="$(SITE2_DATAFRAME)" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS) \
		--workspace ../../testing/runs/workspace

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app and invokes
# `make run`. This Client API tutorial has no legacy testing/ harness path, so `run` prepares the checkpoint
# and delegates to `sim` (the recipe/simulator path via job.py).
run: prepare-checkpoint sim

clean:
	rm -rf ./fl_job
