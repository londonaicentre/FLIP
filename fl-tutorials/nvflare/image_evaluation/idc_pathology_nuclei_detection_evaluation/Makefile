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

# Run in the flip-utils environment with the `full` ML extra -- the same package set the flare-fl-base
# image installs (`uv sync --extra full`), so a local SimEnv run matches the deployed image. A bare
# `uv run` would resolve to the repo-root venv, which has no nvflare/monai/pydicom.
#
# This tutorial deliberately adds NO dependency to that set: the detector is classical (scikit-image
# + scipy, both already present via monai[skimage,scipy]) and DICOM decoding uses pydicom with the
# Pillow JPEG 2000 handler. That is what lets it run on the published FL images with no rebuild.
FLIP_UTILS := $(abspath $(APP_DIR)/../../../../flip-utils)
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
ifneq ("$(wildcard .env.app)","")
include ./.env.app
# Export only real assignments. A plain `sed 's/=.*//'` also emits words from comment lines, so a
# comment that merely mentions `VAR=value` would `export VAR`, defining it empty -- and an empty
# definition silently defeats a later `VAR ?= default`.
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' ./.env.app)
endif

SPECIFICATION := $(APP_DIR)/app_files/detector_specification.pt

N_CLIENTS ?= 2

# Resolve a (possibly empty) path against this app folder, so .env.app can use app-relative paths
# regardless of the directory make is invoked from. `override` makes it apply to command-line values.
abspath_app = $(if $(strip $(1)),$(abspath $(if $(filter /%,$(strip $(1))),$(strip $(1)),$(APP_DIR)/$(strip $(1)))))

override SITE1_IMAGES_DIR := $(call abspath_app,$(SITE1_IMAGES_DIR))
override SITE1_DATAFRAME  := $(call abspath_app,$(SITE1_DATAFRAME))
override SITE2_IMAGES_DIR := $(call abspath_app,$(SITE2_IMAGES_DIR))
override SITE2_DATAFRAME  := $(call abspath_app,$(SITE2_DATAFRAME))

.PHONY: run sim export prepare-model check-data summary overlays clean

# --- Detector specification ---
# The FL server broadcasts this to every site. It is derived from app_files/config.json, so it is
# regenerated whenever that changes rather than committed.
prepare-model:
	@$(UV_RUN) python "$(APP_DIR)/process_tools/build_specification.py" --output "$(SPECIFICATION)"

# --- Dataset guard ---
# Fail with the command that fixes it, rather than letting the evaluator die on a missing file
# several minutes into a run.
check-data:
	@if [ ! -f "$(SITE1_DATAFRAME)" ] || [ ! -f "$(SITE2_DATAFRAME)" ]; then \
		echo "Tutorial data not found."; \
		echo "  expected: $(SITE1_DATAFRAME)"; \
		echo "            $(SITE2_DATAFRAME)"; \
		echo ""; \
		echo "Fetch it with:  make -C ../../.. download-idc-pathology-data"; \
		echo "(~2.1 GB from the NCI Imaging Data Commons public buckets; no credentials needed)"; \
		exit 1; \
	fi

# --- Export job config (no GPU, no data) ---
export: prepare-model
	$(UV_RUN) python job.py --export --export-dir ./fl_job --n_clients $(N_CLIENTS)

# --- SimEnv local simulation ---
# site-1/site-2 read distinct slide sets; the evaluator picks its own via flare.get_site_name().
# SITE*_ are absolute by now because simulator workers run from the workspace dir, not here.
sim: check-data
	SITE1_IMAGES_DIR="$(SITE1_IMAGES_DIR)" SITE1_DATAFRAME="$(SITE1_DATAFRAME)" \
	SITE2_IMAGES_DIR="$(SITE2_IMAGES_DIR)" SITE2_DATAFRAME="$(SITE2_DATAFRAME)" \
	$(UV_RUN) python job.py --n_clients $(N_CLIENTS)

# --- Federated summary ---
# Derives the pooled figures from the per-site counts the job returned, and prints the table. Split
# out of the job because the arithmetic is the lesson: it reads far better as a short script than as
# a custom NVFLARE aggregation component, and it needs nothing that did not already leave the sites.
summary:
	@$(UV_RUN) python "$(APP_DIR)/process_tools/summarise_results.py"

# --- Nuclei overlays ---
# Renders the scored tiles with reference outlines and detections, colour-coded by outcome, and
# assembles them into an mp4. Local and single-site by design: per-nucleus overlays are exactly the
# kind of data the federated job never returns, so this is a debugging and explanation aid, not part
# of the job. ACCESSION picks the slide; TILES how many frames.
ACCESSION ?= TCGA-A8-A0AB
TILES ?= 12
overlays: check-data
	@$(UV_RUN) --group dev python "$(APP_DIR)/process_tools/render_overlays.py" \
		--accession $(ACCESSION) --tiles $(TILES) --video

# --- Tutorial-harness entrypoint ---
# fl-tutorials/nvflare/Makefile auto-discovers any dir with a .env.app and invokes `make run`.
run: prepare-model sim summary

clean:
	rm -rf ./fl_job ./results
