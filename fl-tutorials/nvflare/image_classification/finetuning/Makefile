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
REPO_ROOT := $(abspath $(APP_DIR)/../../../..)

# Run job.py in the flip-utils environment with the `full` ML extra — the same package set the
# flare-fl-base FL image installs, so a local SimEnv run matches the deployed image.
FLIP_UTILS := $(REPO_ROOT)/flip-utils
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed 's/=.*//' ./.env.app)
endif

# Paper-replication defaults: 2 clients, 50 global rounds (local epochs / batch size /
# LR come from app_files/config.json, exactly as in the production run).
N_CLIENTS ?= 2
ROUNDS ?= 50
WORKSPACE ?= /tmp/nvflare/finetuning
METRICS_OUT ?= $(REPO_ROOT)/model_metrics

# Per-site cohort env (SITE1_*/SITE2_*) is forwarded only when set; data_utils gives it
# precedence over config.json SITE_DATA and the single-source DEV_* fallback.
SIM_ENV := DEV_IMAGES_DIR="$(abspath $(DEV_IMAGES_DIR))" DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))"
ifneq ($(strip $(SITE1_IMAGES_DIR)),)
SIM_ENV += SITE1_IMAGES_DIR="$(abspath $(SITE1_IMAGES_DIR))" SITE1_DATAFRAME="$(abspath $(SITE1_DATAFRAME))"
endif
ifneq ($(strip $(SITE2_IMAGES_DIR)),)
SIM_ENV += SITE2_IMAGES_DIR="$(abspath $(SITE2_IMAGES_DIR))" SITE2_DATAFRAME="$(abspath $(SITE2_DATAFRAME))"
endif

.PHONY: run export sim metrics clean

# --- Tutorial-harness entrypoint ---
# The shared harness (fl-tutorials/nvflare/Makefile) auto-discovers any dir with a .env.app
# and invokes `make run`. The Client API variant runs via the recipe/simulator path (job.py).
run: sim

# --- Export job config (no GPU needed) ---
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(ROUNDS)

# --- SimEnv local simulation (requires GPU + data; see .env.app) ---
sim:
	$(SIM_ENV) $(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(ROUNDS) \
		--workspace $(WORKSPACE)

# --- Extract the production-equivalent round metrics from the simulator logs ---
# Same rounds.tsv/summary.md/boxplot outputs as scripts/extract_model_metrics.sh.
# Optional: COMPARE=/path/to/platform/rounds.tsv adds a side-by-side overhead table.
metrics:
	bash $(REPO_ROOT)/scripts/extract_simulator_metrics.sh "$(WORKSPACE)" \
		-o "$(METRICS_OUT)" $(if $(COMPARE),--compare "$(COMPARE)")

clean:
	rm -rf ./fl_job
