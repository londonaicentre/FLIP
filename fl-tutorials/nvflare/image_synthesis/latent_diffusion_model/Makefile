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
export $(shell sed -e '/^[[:space:]]*#/d' -e 's/=.*//' ./.env.app)
endif

# --- Local-run knobs ---
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=2`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=latent_diffusion_model
# NUM_ROUNDS=2`), or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from
# config.json at submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 1
N_CLIENTS ?= 2

# --- Where debug images land (local runs only) ---
# app_files/debug_samples/ inside the simulator's per-site job workspace is the default and the
# production behaviour, but under /tmp/nvflare/... it is awkward to find and the next run wipes it.
# For local runs put it beside the datasets instead, under the same gitignored fl-tutorials/data/.
# Never set on a trust: config.json travels with the app, this does not.
DEBUG_SAMPLES_DIR ?= $(FL_TUTORIALS)/data/debug_samples/latent_diffusion_model

# --- Frozen autoencoder checkpoint ---
# Must match config.json's SERVER_CHECKPOINT and job.py's _CHECKPOINT_NAME.
PROCESSED_CHECKPOINT ?= app_files/pretrained_autoencoder.pt
# Point this at a finished `autoencoder` tutorial run's checkpoint (the downloaded FL global model,
# or the simulator's FL_global_model.pt) to build PROCESSED_CHECKPOINT from it.
RAW_CHECKPOINT ?=

.PHONY: run export sim prepare-checkpoint clean

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py).
run: prepare-checkpoint sim

# --- Autoencoder checkpoint preparation ---
# This job trains only the diffusion model; the autoencoder is supplied, frozen, as a checkpoint at
# app_files/pretrained_autoencoder.pt. InitialCheckpointPTModelPersistor loads it (strict=False) into
# the round-0 global model and broadcasts it, so clients never need the file. `prepare-checkpoint` is
# a no-op if it already exists.
prepare-checkpoint:
	@if [ -f "$(PROCESSED_CHECKPOINT)" ]; then \
		echo "✅ Autoencoder checkpoint found at $(PROCESSED_CHECKPOINT)."; \
	elif [ -z "$(RAW_CHECKPOINT)" ]; then \
		echo "❌ No autoencoder checkpoint at $(PROCESSED_CHECKPOINT), and RAW_CHECKPOINT is not set."; \
		echo ""; \
		echo "This tutorial trains a diffusion model inside a FROZEN autoencoder's latent space,"; \
		echo "so it needs that autoencoder up front. Produce one by running the sibling tutorial:"; \
		echo ""; \
		echo "    make -C fl-tutorials run-tutorial TUTORIAL=autoencoder"; \
		echo ""; \
		echo "then point RAW_CHECKPOINT at its result and re-run:"; \
		echo ""; \
		echo "    make prepare-checkpoint RAW_CHECKPOINT=<path to that run's .pt>"; \
		echo ""; \
		exit 1; \
	else \
		echo "Extracting the autoencoder from $(RAW_CHECKPOINT) ..."; \
		$(UV_RUN) python process_tools/extract_autoencoder.py \
			--src "$(RAW_CHECKPOINT)" \
			--dst "$(PROCESSED_CHECKPOINT)"; \
		echo "✅ Wrote $(PROCESSED_CHECKPOINT)"; \
	fi

# --- Export job config (no GPU needed) ---
# Builds a complete NVFLARE job under ./fl_job/flip_fedavg/ including meta.json,
# app/config/, and app/custom/ (bundled flip/ package + staged app_files/).
# FLIP-specific values (project_id, query) are injected via environment variables.
# Deliberately does NOT depend on prepare-checkpoint: exporting is for reviewing the job wiring and
# works without the checkpoint (it is staged only if present).
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

# --- SimEnv local simulation (requires GPU + data + checkpoint) ---
# Runs the job under the NVFLARE simulator. Requires a GPU and the reference
# dataset (run `make -C fl-tutorials download-xray-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
sim:
	@echo "Running tutorial with: n_clients=$(N_CLIENTS), num_rounds=$(NUM_ROUNDS)"
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	DEBUG_SAMPLES_DIR="$(abspath $(DEBUG_SAMPLES_DIR))" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

clean:
	rm -rf ./fl_job
