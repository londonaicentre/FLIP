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

# Run job.py in the flip-utils environment with the `nvflare`, `full` and `segmentation` extras — the package set the
# flare-fl-client FL image installs (the client alone carries `segmentation`, which is TotalSegmentator), so a local
# SimEnv run matches the deployed image. A bare `uv run` would resolve to the repo-root venv, which has no
# torch/nvflare/monai.
FLIP_UTILS := $(abspath $(APP_DIR)/../../../../flip-utils)
UV_RUN := uv run --project $(FLIP_UTILS) --extra nvflare --extra full --extra segmentation

# --- Load app-specific env ---
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed 's/=.*//' ./.env.app)
endif

.PHONY: run export sim download-weights overlay-figure clean

# --- Weights download ---
# TotalSegmentator's weights are not uploaded with the app and are never fetched at run time: at a trust they are in
# the FL client image, and here they are staged once, host-side, into the gitignored data root. config.json is
# written first so TotalSegmentator keeps it: its own would turn usage statistics on.
download-weights:
	@set -e; \
	dst="$(abspath $(TOTALSEG_HOME_DIR))"; \
	mkdir -p "$$dst"; \
	if [ ! -f "$$dst/config.json" ]; then \
		printf '{\n    "totalseg_id": "flip",\n    "send_usage_stats": false,\n    "prediction_counter": 0\n}\n' > "$$dst/config.json"; \
	fi; \
	TOTALSEG_HOME_DIR="$$dst" $(UV_RUN) totalseg_download_weights -t total

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app and
# invokes `make run`, which delegates to `sim` (the recipe/simulator path via job.py) after
# ensuring the weights are present.
run: download-weights sim

# --- Export job config (no GPU needed) ---
# Builds a complete FLARE job under ./fl_job/flip_inference/ including meta.json, app/config/, and
# app/custom/ (bundled flip/ package + staged app_files/). No weights are needed: the job carries none.
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients 2

# --- SimEnv local simulation (requires GPU + data + weights) ---
# Runs the job under the FLARE simulator. Requires a GPU and the reference dataset
# (run `make -C fl-tutorials download-spleen-data` first).
# FLIP-specific values are read from FLIP_PROJECT_ID and FLIP_QUERY env vars.
# DEV_IMAGES_DIR/DEV_DATAFRAME are resolved to absolute paths so the simulator's client workers
# (which run from the workspace dir, not here) find the LOCAL_DEV dataset. The same goes for the weights
# and for DEV_MASKS_DIR, where a local run keeps its masks (at a trust they go to XNAT).
sim:
	DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	TOTALSEG_HOME_DIR="$(abspath $(TOTALSEG_HOME_DIR))" \
	DEV_MASKS_DIR="$(abspath $(DEV_MASKS_DIR))" \
	$(UV_RUN) python job.py \
		--n_clients 2

# --- README figure (after a simulator run) ---
overlay-figure:
	$(UV_RUN) --with matplotlib python docs/make_overlay_figure.py \
		--images-dir "$(abspath $(DEV_IMAGES_DIR))" --masks-dir "$(abspath $(DEV_MASKS_DIR))"

clean:
	rm -rf ./fl_job
