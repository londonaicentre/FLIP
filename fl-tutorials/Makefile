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

# NVFLARE tutorial runner. Each tutorial under <category>/<name>/ carries a Makefile
# + .env.app that delegates to the shared harness in testing/ (app_organiser.sh +
# compose.yml -> nvflare simulator). Requires GPUs + the flare-fl-base image, and the
# tutorial's dataset downloaded first (see each tutorial's README).
#
#   make download-xray-data                       # fetch the x-ray dataset from HF
#   make download-spleen-data                      # fetch + build the MSD spleen dataset
#   make list-tutorials
#   make run-tutorial TUTORIAL=xray_classification
#   make run-all-tutorials      # runs every tutorial (heavy; stops on first failure)
#
# Datasets land (gitignored) under data/ where each tutorial's .env.app expects them:
#   - xray_classification: reference dataset from Hugging Face (download-xray-data)
#   - the three spleen tutorials (segmentation / evaluation / synthesis) share the MSD
#     spleen dataset (download-spleen-data); override case count with NUM_CASES=<n>.

# Discover tutorials: any <category>/<name>/ that carries a .env.app.
TUTORIAL_DIRS := $(sort $(patsubst %/,%,$(dir $(wildcard */*/.env.app))))

# Hugging Face dataset backing the xray_classification tutorial.
XRAY_DATA_REPO := aicentreflip/flip-fl-base-test-data
# Number of MSD spleen cases to download for the spleen tutorials (1-41).
NUM_CASES ?= 10
SPLEEN_DIR := image_segmentation/3d_spleen_segmentation

.PHONY: list-tutorials run-tutorial run-all-tutorials download-xray-data download-spleen-data

# Download the x-ray reference dataset from Hugging Face and normalise it into the
# layout image_classification/xray_classification/.env.app expects (data/xrays_mini_300/,
# gitignored). Idempotent — safe to re-run.
download-xray-data:
	@echo "⬇️  Downloading x-ray dataset from Hugging Face ($(XRAY_DATA_REPO))..."
	@uv run --no-project --with huggingface_hub python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$(XRAY_DATA_REPO)', repo_type='dataset', local_dir='data/.hf', allow_patterns=['flip-fl-base-test-data/xrays_mini_300/*'])"
	@rm -rf data/xrays_mini_300
	@mkdir -p data/xrays_mini_300
	@cp -r data/.hf/flip-fl-base-test-data/xrays_mini_300/accession-resources data/xrays_mini_300/accession-resources
	@cp data/.hf/flip-fl-base-test-data/xrays_mini_300/sample_get_dataframe_response.csv data/xrays_mini_300/dataframe.csv
	@echo "✅ x-ray data ready at data/xrays_mini_300/ — run: make run-tutorial TUTORIAL=xray_classification"

# Download the MSD spleen dataset and build its accession CSV into data/spleen/ (gitignored),
# shared by the segmentation / evaluation / synthesis tutorials. Uses the spleen tutorial's own
# uv project for MONAI. Large download — overridable via NUM_CASES (1-41, default 10).
download-spleen-data:
	@echo "⬇️  Downloading MSD spleen dataset ($(NUM_CASES) cases) — large, uses the spleen tutorial's deps..."
	@cd $(SPLEEN_DIR) && uv run python utils/download_spleen_dataset.py --output_dir ../../data/spleen/images --num_cases $(NUM_CASES)
	@cd $(SPLEEN_DIR) && uv run python utils/create_spleen_accession_csv.py --images_dir ../../data/spleen/images --output_csv ../../data/spleen/dataframe.csv
	@echo "✅ spleen data ready at data/spleen/ — run: make run-tutorial TUTORIAL=3d_spleen_segmentation"

list-tutorials:
	@echo "Available NVFLARE tutorials (pass the name to 'make run-tutorial TUTORIAL=…'):"
	@for d in $(TUTORIAL_DIRS); do echo "  - $$(basename $$d)  ($$d)"; done

run-tutorial:
	@dir="$(strip $(foreach d,$(TUTORIAL_DIRS),$(if $(filter $(TUTORIAL),$(notdir $(d))),$(d))))"; \
	if [ -z "$(TUTORIAL)" ] || [ -z "$$dir" ]; then \
		echo "Set TUTORIAL=<name>. Available:"; \
		for d in $(TUTORIAL_DIRS); do echo "  - $$(basename $$d)"; done; \
		exit 1; \
	fi; \
	echo "🚀 Running NVFLARE tutorial '$(TUTORIAL)' ($$dir)"; \
	$(MAKE) -C "$$dir" run

run-all-tutorials:
	@for d in $(TUTORIAL_DIRS); do \
		echo "🚀 Running NVFLARE tutorial $$(basename $$d)"; \
		$(MAKE) -C "$$d" run || { echo "❌ tutorial $$(basename $$d) failed"; exit 1; }; \
	done
