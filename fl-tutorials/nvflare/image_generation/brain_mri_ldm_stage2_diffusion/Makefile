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
# `diffusion` adds matplotlib for ../dev/plot_utils.py; no FL image has it.
UV_RUN := uv run --project $(FLIP_UTILS) --extra full --group diffusion

# --- Load app-specific env ---
# Comment lines must be dropped before building the export list: a leading `#` token would start a
# make comment inside the `export` directive and silently un-export every variable listed after it.
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed -e '/^[[:space:]]*\#/d' -e 's/=.*//' ./.env.app)
endif

# --- Local-run knobs ---
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=2`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=brain_mri_ldm_stage2_diffusion
# NUM_ROUNDS=2`), or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from
# config.json at submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 1
N_CLIENTS ?= 2

# --- Where debug images land (local runs only) ---
# Output dir for sample images, beside the datasets under the gitignored fl-tutorials/data/. Also half
# of the simulator check: images are written only with SAVE_DEBUG_SAMPLES true in config.json AND
# LOCAL_DEV true AND this set (plot_utils.samples_enabled). Never set on a trust, which also runs
# with LOCAL_DEV=false, so production writes nothing whatever config.json says.
DEBUG_SAMPLES_DIR ?= $(FL_TUTORIALS)/data/debug_samples/brain_mri_ldm_stage2_diffusion

# --- Frozen autoencoder checkpoint ---
# Must match config.json's SERVER_CHECKPOINT and job.py's _CHECKPOINT_NAME. extract_autoencoder.py
# writes autoencoder_config.yaml beside it: the architecture clients build and the weights'
# fingerprint the trainer checks. The two files are one artifact; prepare them together.
PROCESSED_CHECKPOINT ?= app_files/pretrained_autoencoder.pt
# Point this at a finished `brain_mri_ldm_stage1_autoencoder` tutorial run's checkpoint (the downloaded FL global model,
# or the simulator's FL_global_model.pt) to build PROCESSED_CHECKPOINT from it.
RAW_CHECKPOINT ?=
# The config.json that run was trained with. Defaults to the autoencoder tutorial's own, so set it
# whenever the run's uploaded config.json changed the network.
RAW_CONFIG ?=

.PHONY: run export sim prepare-checkpoint clean

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py).
run: prepare-checkpoint sim

# --- Autoencoder checkpoint preparation ---
# This job trains only the diffusion model. The autoencoder arrives frozen: the checkpoint is loaded on
# the FL server and broadcast in round 0, and every client builds the bare network from the YAML.
prepare-checkpoint:
ifneq ($(RAW_CHECKPOINT),)
	$(UV_RUN) python process_tools/extract_autoencoder.py --src "$(RAW_CHECKPOINT)" \
		--dst "$(PROCESSED_CHECKPOINT)" $(if $(RAW_CONFIG),--autoencoder-config "$(RAW_CONFIG)")
else
	@test -f "$(PROCESSED_CHECKPOINT)" || { \
		echo "❌ No $(PROCESSED_CHECKPOINT). This tutorial trains inside a frozen autoencoder's latent space."; \
		echo "   Train one with the sibling tutorial, then extract it:"; \
		echo "     make -C fl-tutorials run-tutorial TUTORIAL=brain_mri_ldm_stage1_autoencoder"; \
		echo "     make prepare-checkpoint RAW_CHECKPOINT=/tmp/nvflare/brain_mri_ldm_stage1_autoencoder/flip_fedavg/server/simulate_job/FL_global_model.pt"; \
		exit 1; }
endif

# --- Export job config (no GPU needed) ---
# Builds a complete FLARE job under ./fl_job/flip_fedavg/ including meta.json,
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
# Runs the job under the FLARE simulator. Requires a GPU and the reference
# dataset (run `make -C fl-tutorials download-brain-mri-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
# A local run does NUM_ROUNDS rounds; GLOBAL_ROUNDS in config.json is what the platform runs.
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
