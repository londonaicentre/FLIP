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

# `make plan` — nnU-Net's fingerprint extraction + experiment planning over EVERY participating
# site at once, then the resulting plan is copied into app/ where models.get_model() reads it.
#
# Plan once, pooled, and ship the same file to every site: planned per site, the centres land on
# different U-Net topologies (see README "nnU-Net plans"), and weights of different shapes cannot be
# averaged. The site folders are the partition-prostate-data output (data/prostate/sites/<CENTER>,
# the dcm2niix-converted tree) — or prepare-prostate-local-data's `--site-tree` when there is no
# Docker to run dcm2niix, which yields the same fingerprint (spacing, cropped shape and foreground
# intensities do not depend on storage order).
DATA_ROOT ?= ../../data/prostate
PLAN_SITES ?= $(DATA_ROOT)/sites/ZGT $(DATA_ROOT)/sites/PCNN $(DATA_ROOT)/sites/RUMC
PLAN_MODALITY ?= t2w
PLAN_GPU_MEMORY_GB ?= 24
PLAN_PROCESSES ?= 8
PLAN_OUT := configs

.PHONY: plan
plan:
	@for site in $(PLAN_SITES); do [ -d "$$site" ] || { echo "❌ site folder missing: $$site (run make -C fl-tutorials partition-prostate-data)"; exit 1; }; done
	@mkdir -p $(PLAN_OUT)
	uv run --group planning python calculate_dataset_fingerprint_segmentation.py \
		--site-dir $(PLAN_SITES) --output-dir $(PLAN_OUT) --modality $(PLAN_MODALITY) \
		--num-processes $(PLAN_PROCESSES) --gpu-memory-GB $(PLAN_GPU_MEMORY_GB)
	@cp $(PLAN_OUT)/nnUNetPlans_segmentation.json app/nnUNetPlans_segmentation.json
	@echo "✅ plan written to app/nnUNetPlans_segmentation.json — commit it, and record the sites/date in README.md"
