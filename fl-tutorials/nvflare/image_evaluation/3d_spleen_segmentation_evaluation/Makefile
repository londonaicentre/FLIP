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
FLIP_UTILS := $(abspath $(APP_DIR)/../../../../flip-utils)
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed 's/=.*//' ./.env.app)
endif

.PHONY: run export sim download-checkpoints clean

# --- Checkpoint download ---
# The evaluation app needs the uploaded model checkpoint in app_files/ (the server loads it and
# broadcasts the weights to clients over the validate task).
download-checkpoints:
	@set -e; \
	ckpt="$(notdir $(MODEL_CHECKPOINT_URL))"; \
	dst="$(APP_DIR)/app_files/$$ckpt"; \
	echo "Checking for: $$dst"; \
	if [ ! -f "$$dst" ]; then \
		echo "Downloading from $(MODEL_CHECKPOINT_URL) ..."; \
		curl -fL "$(MODEL_CHECKPOINT_URL)" -o "$$dst"; \
	else \
		echo "Checkpoint file $$ckpt already exists, skipping download."; \
	fi

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app and
# invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py) after
# ensuring the checkpoint is present.
run: download-checkpoints sim

# --- Export job config (no GPU needed) ---
# Builds a complete NVFLARE job under ./fl_job/flip_evaluation/ including meta.json, app/config/, and
# app/custom/ (bundled flip/ package + staged app_files/ incl. the model.pt checkpoint).
export: download-checkpoints
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients 2

# --- SimEnv local simulation (requires GPU + data + checkpoint) ---
# Runs the job under the NVFLARE simulator. Requires a GPU and the reference dataset
# (run `make -C fl-tutorials download-spleen-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
sim:
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	$(UV_RUN) python job.py \
		--n_clients 2

clean:
	rm -rf ./fl_job
