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
#
# Flower tutorial runner, mirroring FLARE's: run-tutorial and run-all-tutorials run the
# flwr SIMULATOR (sim-tutorial.sh: no SuperLink container, no SuperNodes, no fl-api, no Docker),
# and sim-tutorial is the same target under its "no containers" name. The deployment path (the
# FL images, fl-api submit, SuperNode registration) is `make e2e_smoke FL_BACKEND=flower` on the
# dev stack, as for FLARE; fl-services/flower's `make up` / `make submit` runs the containers
# by hand.
# The root `make -C fl-tutorials … FL_BACKEND=flower` forwards here.
#
#   make list-tutorials
#   make run-tutorial TUTORIAL=xray_classification       # simulator
#   make run-all-tutorials                                # every tutorial on the simulator (the release check)
#
# Every tutorial needs its dataset downloaded first —
# `make -C fl-tutorials download-<dataset>-data FL_BACKEND=flower`. The dataset tooling is
# shared with the FLARE harness in fl-tutorials/datasets/ and lands under the shared
# gitignored fl-tutorials/data/, which sim-tutorial.sh reads.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# Tutorials = immediate subdirs carrying an app/ (xray_classification, 3d_spleen_segmentation,
# 3d_spleen_segmentation_evaluation, 3d_prostate_segmentation, ehr_risk_prediction).
TUTORIAL_DIRS := $(sort $(patsubst %/app/,%,$(dir $(wildcard */app/.))))

.PHONY: list-tutorials run-tutorial run-all-tutorials sim-tutorial

list-tutorials:
	@echo "Available Flower tutorials (pass the name to 'make run-tutorial TUTORIAL=…'):"
	@for d in $(TUTORIAL_DIRS); do echo "  - $$d"; done

# Run one tutorial on the flwr simulator. Same app code as a platform run (identity comes from
# node_config; see flip.flower.identity), and the exit status is the run's terminal status.
run-tutorial:
	@bash $(REPO_ROOT)/fl-tutorials/flower/sim-tutorial.sh "$(TUTORIAL)"

# FLARE's name for the same thing: `sim-tutorial` means "no containers" on both backends.
sim-tutorial: run-tutorial

run-all-tutorials:
	@for d in $(TUTORIAL_DIRS); do \
		echo "🚀 Running Flower tutorial $$d"; \
		bash $(REPO_ROOT)/fl-tutorials/flower/sim-tutorial.sh "$$d" || { echo "❌ tutorial $$d failed"; exit 1; }; \
	done
