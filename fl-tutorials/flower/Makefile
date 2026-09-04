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
# Flower tutorial runner. Unlike NVFLARE (which runs on the simulator), Flower
# tutorials run on the standalone compose stack from fl-services/flower (SuperLink
# + 2 SuperNodes + fl-api): run-tutorial brings it up with the tutorial's dev data
# bind-mounted (LOCAL_DEV), submits the job to the fl-api control plane, waits, and
# tears down. The root `make -C fl-tutorials … FL_BACKEND=flower` forwards here.
#
#   make list-tutorials
#   make run-tutorial TUTORIAL=xray_classification
#   make run-tutorial TUTORIAL=3d_spleen_segmentation
#   make run-all-tutorials
#
# Every tutorial needs its dataset downloaded first —
# `make -C fl-tutorials download-<dataset>-data FL_BACKEND=flower`. The dataset tooling is
# shared with the NVFLARE harness in fl-tutorials/datasets/ and lands under the shared
# gitignored fl-tutorials/data/, which run-tutorial.sh bind-mounts into the stack.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# Tutorials = immediate subdirs carrying an app/ (xray_classification,
# 3d_spleen_segmentation, 3d_spleen_segmentation_evaluation).
TUTORIAL_DIRS := $(sort $(patsubst %/app/,%,$(dir $(wildcard */app/.))))

.PHONY: list-tutorials run-tutorial run-all-tutorials sim-tutorial

list-tutorials:
	@echo "Available Flower tutorials (pass the name to 'make run-tutorial TUTORIAL=…'):"
	@for d in $(TUTORIAL_DIRS); do echo "  - $$d"; done

# Run one tutorial on the standalone Flower stack (up → submit → wait → down).
# Every tutorial needs its dataset first:
# `make -C fl-tutorials download-<dataset>-data FL_BACKEND=flower`.
run-tutorial:
	@bash $(REPO_ROOT)/fl-tutorials/flower/run-tutorial.sh "$(TUTORIAL)"

# Run one tutorial on the flwr SIMULATOR — no SuperLink, no SuperNodes, no fl-api, no
# Docker. Same app code as a platform run (identity comes from node_config; see
# flip.flower.identity). The NVFLARE counterpart is `make sim` in a tutorial dir.
sim-tutorial:
	@bash $(REPO_ROOT)/fl-tutorials/flower/sim-tutorial.sh "$(TUTORIAL)"

run-all-tutorials:
	@for d in $(TUTORIAL_DIRS); do \
		bash $(REPO_ROOT)/fl-tutorials/flower/run-tutorial.sh "$$d" || { echo "❌ tutorial $$d failed"; exit 1; }; \
	done
