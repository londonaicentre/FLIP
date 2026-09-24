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
# Local-sim/export values passed to job.py; override per invocation (`make run NUM_ROUNDS=10`),
# via the harness (`make -C fl-tutorials run-tutorial TUTORIAL=autoencoder
# NUM_ROUNDS=10`), or in .env.app. Local only: in production the FL API reads GLOBAL_ROUNDS from
# config.json at submit time and overrides whatever the recipe baked in.
NUM_ROUNDS ?= 1
N_CLIENTS ?= 2

# --- Where debug images land (local runs only) ---
# app_files/debug_samples/ inside the simulator's per-site job workspace is the default and the
# production behaviour, but under /tmp/nvflare/... it is awkward to find and the next run wipes it.
# For local runs put it beside the datasets instead, under the same gitignored fl-tutorials/data/.
# Never set on a trust: config.json travels with the app, this does not.
DEBUG_SAMPLES_DIR ?= $(FL_TUTORIALS)/data/debug_samples/autoencoder

# --- Shipped weights (FLIP#1206) ---
# The perceptual loss's SqueezeNet backbone, which lpips would otherwise fetch through
# torch.hub on first use. FL apps never download at run time, so the tutorial ships it: the
# download is shared (`make -C fl-tutorials download-weights ARCH=medicalnet_resnet50`, hash-checked)
# and `weights` copies the checkpoint flat into app_files/, which is what gets uploaded with
# the code and what job.py bundles into every site's custom/ for the simulator and `export`.
# trainer.py stages it into its torch.hub dir at start-up. Gitignored. The filename must be
# the one torchvision asks for (trainer.py checks that at run time, and the tests pin it).
# This tutorial owns it because this is where the perceptual loss is: the diffusion tutorials
# score in latent/noise space and construct no PerceptualLoss at all.
HUB_CHECKPOINT := medicalnet_resnet50_23datasets-ff48a622.pth
SHARED_CHECKPOINT := $(FL_TUTORIALS)/data/weights/$(HUB_CHECKPOINT)

.PHONY: run export sim clean weights

weights: app_files/$(HUB_CHECKPOINT)

app_files/$(HUB_CHECKPOINT): $(SHARED_CHECKPOINT)
	cp "$<" "$@"

$(SHARED_CHECKPOINT):
	$(MAKE) --no-print-directory -C $(FL_TUTORIALS)/datasets download-weights ARCH=medicalnet_resnet50

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py).
run: sim

# --- Export job config (no GPU needed) ---
# Builds a complete NVFLARE job under ./fl_job/flip_fedavg/ including meta.json,
# app/config/, and app/custom/ (bundled flip/ package + staged app_files/).
# FLIP-specific values (project_id, query) are injected via environment variables. Both bundling
# paths (this and sim) need the shipped backbone in app_files/ first — hence `weights`.
export: weights
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

# --- SimEnv local simulation (requires GPU + data) ---
# Runs the job under the NVFLARE simulator. Requires a GPU and the reference
# dataset (run `make -C fl-tutorials download-xray-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
sim: weights
	@echo "Running tutorial with: n_clients=$(N_CLIENTS), num_rounds=$(NUM_ROUNDS)"
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	DEBUG_SAMPLES_DIR="$(abspath $(DEBUG_SAMPLES_DIR))" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

clean:
	rm -rf ./fl_job
