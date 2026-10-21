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
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=2`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=latent_diffusion_model
# NUM_ROUNDS=2`), or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from
# config.json at submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 1
N_CLIENTS ?= 2

# --- Where debug images land (local runs only) ---
# Output dir for sample images, beside the datasets under the gitignored fl-tutorials/data/. Also half
# of the simulator check: images are written only with SAVE_DEBUG_SAMPLES true in config.json AND
# LOCAL_DEV true AND this set (plot_utils.samples_enabled). Never set on a trust, which also runs
# with LOCAL_DEV=false, so production writes nothing whatever config.json says.
DEBUG_SAMPLES_DIR ?= $(FL_TUTORIALS)/data/debug_samples/latent_diffusion_model

# --- Frozen autoencoder checkpoint ---
# Must match config.json's SERVER_CHECKPOINT and job.py's _CHECKPOINT_NAME.
PROCESSED_CHECKPOINT ?= app_files/pretrained_autoencoder.pt
# The architecture the checkpoint was trained with, written beside it by extract_autoencoder.py and
# read by app_files/models.py. Unlike the checkpoint this is NOT server-only: clients build the bare
# autoencoder, so it ships with the app. The two are one artifact — never stage one without the other.
PROCESSED_CONFIG ?= app_files/autoencoder_config.yaml
# Point this at a finished `autoencoder` tutorial run's checkpoint (the downloaded FL global model,
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
# This job trains only the diffusion model; the autoencoder is supplied, frozen, as a checkpoint at
# app_files/pretrained_autoencoder.pt plus the architecture that describes it at
# app_files/autoencoder_config.yaml. InitialCheckpointPTModelPersistor loads the weights
# (strict=False) into the round-0 global model and broadcasts them, so clients never need that file —
# but they do need the YAML, to build the bare autoencoder those weights land on.
#
# When both are already present this VERIFIES them rather than skipping: it instantiates the recorded
# architecture and compares it to the checkpoint's tensors. A differently-shaped checkpoint would
# otherwise be silently declined by the persistor's strict=False load — a randomly-initialised
# "pretrained" encoder that trains and reports plausible losses.
prepare-checkpoint:
	@if [ -f "$(PROCESSED_CHECKPOINT)" ] && [ -f "$(PROCESSED_CONFIG)" ]; then \
		$(UV_RUN) python process_tools/extract_autoencoder.py \
			--verify-only \
			--dst "$(PROCESSED_CHECKPOINT)" \
			--config-out "$(PROCESSED_CONFIG)"; \
	elif [ -z "$(RAW_CHECKPOINT)" ]; then \
		echo "❌ Need both $(PROCESSED_CHECKPOINT) and $(PROCESSED_CONFIG), and RAW_CHECKPOINT is not set."; \
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
			$(if $(RAW_CONFIG),--autoencoder-config "$(RAW_CONFIG)") \
			--dst "$(PROCESSED_CHECKPOINT)" \
			--config-out "$(PROCESSED_CONFIG)"; \
		echo "✅ Wrote $(PROCESSED_CHECKPOINT) and $(PROCESSED_CONFIG)"; \
	fi

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
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	DEBUG_SAMPLES_DIR="$(abspath $(DEBUG_SAMPLES_DIR))" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

clean:
	rm -rf ./fl_job
