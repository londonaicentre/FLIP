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
# flare-fl-base FL image installs (`uv sync --extra full`; causal-learn is part of it), so a local
# SimEnv run matches the deployed image. A bare `uv run` would resolve to the repo-root venv.
FLIP_UTILS := $(abspath $(APP_DIR)/../../../../flip-utils)
UV_RUN := uv run --project $(FLIP_UTILS) --extra full

# --- Load app-specific env ---
# Comment lines must be dropped before building the export list: a leading `#` token would start a
# make comment inside the `export` directive and silently un-export every variable listed after it.
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed -e '/^[[:space:]]*\#/d' -e 's/=.*//' ./.env.app)
endif

# --- Local-run knobs ---
# pooled_statistics (the default FEDERATION in app_files/config.json) completes in 2 rounds;
# layer_voting needs (max conditioning-set size + 2), e.g. NUM_ROUNDS=6 for sets of up to 4.
# Local only: in production the FL API reads GLOBAL_ROUNDS from config.json at submit time.
NUM_ROUNDS ?= 2
N_CLIENTS ?= 2
# The simulator workspace (override when /tmp/nvflare is not yours) and, for `make summarise`,
# the final model it leaves there.
WORKSPACE ?= /tmp/nvflare/causal
RESULTS ?= $(WORKSPACE)/flip_fedavg/server/simulate_job/FL_global_model.pt

.PHONY: run export sim sim-run clean summarise

# --- Tutorial-harness entrypoint ---
run: sim

# --- Export job config (no GPU, no data needed) ---
export:
	$(UV_RUN) python job.py \
		--export \
		--export-dir ./fl_job \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS)

# --- SimEnv local simulation (CPU) ---
# Requires the cohort built from your own ADNI download (`make -C fl-tutorials convert-adni-cohort
# ADNI_DIR=<IDA tables>`, README "Running on real ADNI data"). Data paths are made absolute because
# the simulator's client workers run from the workspace dir. The two per-site files are the
# converter's 2-way split, so they are passed only for N_CLIENTS=2: with any other count every site
# slices DEV_DATAFRAME by person_id (the two are blanked, since .env.app exports them), and no person
# is at two sites.
#
# The cohort is access-controlled, so a host without it cannot run the tutorial. Run on its own,
# a missing cohort fails with the convert step; under run-all-tutorials (which exports
# TUTORIAL_SKIP_RESTRICTED_IF_ABSENT=1) it is reported as skipped, so the catalogue loop goes on.
SIM_INPUTS := $(if $(filter 2,$(N_CLIENTS)),$(SITE1_DATAFRAME) $(SITE2_DATAFRAME),$(DEV_DATAFRAME))
sim:
	@for f in $(abspath $(SIM_INPUTS)); do \
		[ -e "$$f" ] && continue; \
		if [ -n "$$TUTORIAL_SKIP_RESTRICTED_IF_ABSENT" ]; then \
			echo "⏭️ tutorial biomarker_causal_discovery skipped: access-controlled dataset absent ($$f)"; exit 0; \
		fi; \
		echo "❌ Dataset missing: $$f"; \
		echo "   Build it from your own ADNI download: make -C fl-tutorials convert-adni-cohort ADNI_DIR=<IDA tables>"; \
		exit 1; \
	done; \
	$(MAKE) --no-print-directory sim-run

sim-run:
	@echo "Running tutorial with: n_clients=$(N_CLIENTS), num_rounds=$(NUM_ROUNDS)"
	$(if $(filter 2,$(N_CLIENTS)),SITE1_DATAFRAME="$(abspath $(SITE1_DATAFRAME))" SITE2_DATAFRAME="$(abspath $(SITE2_DATAFRAME))",SITE1_DATAFRAME= SITE2_DATAFRAME=) \
	DEV_DATAFRAME="$(abspath $(DEV_DATAFRAME))" \
	DEV_NUM_PARTITIONS="$(N_CLIENTS)" \
	$(UV_RUN) python job.py \
		--n_clients $(N_CLIENTS) \
		--num_rounds $(NUM_ROUNDS) \
		--workspace $(WORKSPACE)

# --- Read a finished run's model back: edge-frequency table (beside the model) + PAG figure (docs/) ---
# RESULTS defaults to the simulator's final model; point it at a downloaded FL_global_model.pt.
summarise:
	$(UV_RUN) python summarise_results.py "$(RESULTS)" --csv "$(dir $(RESULTS))edge_frequencies.csv" --figure docs/recovered_pag.png $(if $(REFERENCE),--reference "$(REFERENCE)")

clean:
	rm -rf ./fl_job
