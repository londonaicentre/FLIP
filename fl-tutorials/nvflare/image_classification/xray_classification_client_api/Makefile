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

.PHONY: run export sim clean

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`. The Client API variant has no legacy testing/ harness path, so
# `run` delegates to `sim` (the recipe/simulator path via job.py).
run: sim

# --- Export job config (no GPU needed) ---
# Builds a complete NVFLARE job under ./fl_job/flip_fedavg/ including meta.json,
# app/config/, and app/custom/ (bundled flip/ package + staged app_files/).
# FLIP-specific values (project_id, query) are injected via environment variables.
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients 2 \
		--num_rounds 3

# --- SimEnv local simulation (requires GPU + data) ---
# Runs the job under the NVFLARE simulator. Requires a GPU and the reference
# dataset (run `make -C fl-tutorials download-xray-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset.
sim:
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	$(UV_RUN) python job.py \
		--n_clients 2 \
		--num_rounds 3

clean:
	rm -rf ./fl_job
