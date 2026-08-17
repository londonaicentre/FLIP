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
#   make download-arkplus-finetuning-data          # fetch the Ark+ train splits from HF
#   make download-arkplus-eval-data                # fetch the Ark+ hold-out splits from HF
#   make list-tutorials
#   make run-tutorial TUTORIAL=xray_classification
#   make run-all-tutorials      # runs every tutorial (heavy; stops on first failure)
#   make test-template TEMPLATE=fed_opt   # smoke-test a template that has no tutorial
#
# Datasets land (gitignored) under data/ where each tutorial's .env.app expects them:
#   - xray_classification: reference dataset from Hugging Face (download-xray-data)
#   - the spleen tutorials (segmentation legacy + client_api / evaluation / synthesis) share
#     the MSD spleen dataset (download-spleen-data); override case count with NUM_CASES=<n>.
#   - the three Ark+ tutorials share aicentreflip/tutorials-arkplus-cxr-classification:
#     arkplus_fine_tuning uses download-arkplus-finetuning-data (data/arkplus/site{1,2}/); the two
#     Ark+ evaluation tutorials use download-arkplus-eval-data (data/arkplus/site{1,2}_holdoff/).

# Discover tutorials: any <category>/<name>/ that carries a .env.app.
TUTORIAL_DIRS := $(sort $(patsubst %/,%,$(dir $(wildcard */*/.env.app))))

# Hugging Face dataset backing the xray_classification tutorial.
XRAY_DATA_REPO := aicentreflip/flip-fl-base-test-data
# Hugging Face dataset backing the three Ark+ tutorials (site{1,2} train + hold-out splits).
ARKPLUS_DATA_REPO := aicentreflip/tutorials-arkplus-cxr-classification
# Number of MSD spleen cases to download for the spleen tutorials (1-41).
NUM_CASES ?= 10
SPLEEN_DIR := image_segmentation/3d_spleen_segmentation

# Template smoke-test: which fl-apps/nvflare/<template> to exercise, and the tutorial whose
# app_files + data drive it. fed_opt has no tutorial of its own, so it borrows the
# spleen-segmentation tutorial (matching the legacy `make test-spleen-fedopt`).
TEMPLATE ?= fed_opt
TEMPLATE_TUTORIAL ?= image_segmentation/3d_spleen_segmentation

.PHONY: list-tutorials run-tutorial run-all-tutorials download-xray-data download-spleen-data download-arkplus-finetuning-data download-arkplus-eval-data test-template

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

# Download the Ark+ chest X-ray TRAIN splits from Hugging Face and normalise them into the layout
# image_classification/arkplus_fine_tuning/.env.app expects (data/arkplus/site{1,2}/, gitignored).
# Idempotent — safe to re-run. Large (~6.3 GB).
download-arkplus-finetuning-data:
	@echo "⬇️  Downloading Ark+ training splits from Hugging Face ($(ARKPLUS_DATA_REPO))..."
	@uv run --no-project --with huggingface_hub python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$(ARKPLUS_DATA_REPO)', repo_type='dataset', local_dir='data/.hf', allow_patterns=['site1/*', 'site2/*'])"
	@rm -rf data/arkplus/site1 data/arkplus/site2
	@mkdir -p data/arkplus/site1 data/arkplus/site2
	@cp -r data/.hf/site1/accession-resources data/arkplus/site1/accession-resources
	@cp data/.hf/site1/sample_get_dataframe_response.csv data/arkplus/site1/sample_get_dataframe_response.csv
	@cp -r data/.hf/site2/accession-resources data/arkplus/site2/accession-resources
	@cp data/.hf/site2/sample_get_dataframe_response.csv data/arkplus/site2/sample_get_dataframe_response.csv
	@# Written last, only on a fully successful run: `experiment`'s dataset-present check in
	@# arkplus_fine_tuning/Makefile looks for this marker (not just directory existence), so an
	@# interrupted download/copy (e.g. Ctrl+C mid-run) can't be mistaken for complete data on the
	@# next `make reproduce-overhead`.
	@touch data/arkplus/site1/.download-complete data/arkplus/site2/.download-complete
	@echo "✅ Ark+ training data ready at data/arkplus/site{1,2}/ — run: make run-tutorial TUTORIAL=arkplus_fine_tuning"

# Download the Ark+ chest X-ray HOLD-OUT splits from Hugging Face and normalise them into the layout
# the two Ark+ evaluation tutorials' .env.app expect (data/arkplus/site{1,2}_holdoff/, gitignored).
# Idempotent — safe to re-run (~1.6 GB).
download-arkplus-eval-data:
	@echo "⬇️  Downloading Ark+ hold-out splits from Hugging Face ($(ARKPLUS_DATA_REPO))..."
	@uv run --no-project --with huggingface_hub python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$(ARKPLUS_DATA_REPO)', repo_type='dataset', local_dir='data/.hf', allow_patterns=['site1_holdoff/*', 'site2_holdoff/*'])"
	@rm -rf data/arkplus/site1_holdoff data/arkplus/site2_holdoff
	@mkdir -p data/arkplus/site1_holdoff data/arkplus/site2_holdoff
	@cp -r data/.hf/site1_holdoff/accession-resources data/arkplus/site1_holdoff/accession-resources
	@cp data/.hf/site1_holdoff/sample_get_dataframe_response.csv data/arkplus/site1_holdoff/sample_get_dataframe_response.csv
	@cp -r data/.hf/site2_holdoff/accession-resources data/arkplus/site2_holdoff/accession-resources
	@cp data/.hf/site2_holdoff/sample_get_dataframe_response.csv data/arkplus/site2_holdoff/sample_get_dataframe_response.csv
	@echo "✅ Ark+ hold-out data ready at data/arkplus/site{1,2}_holdoff/ — run: make run-tutorial TUTORIAL=arkplus_baseline_classification_evaluation (or arkplus_multimodel_classification_evaluation)"

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

# Smoke-test an fl-apps/nvflare/<template> on the NVFLARE simulator using a tutorial's app_files
# + data. Mainly for templates with no dedicated tutorial (e.g. fed_opt): it runs the
# tutorial with JOB_TYPE overridden to the template, so app_organiser.sh merges
# fl-apps/nvflare/<TEMPLATE>/app with the tutorial's app_files. Requires the dataset
# (make download-spleen-data) + GPUs + the flare-fl-base image.
test-template:
	@echo "🧪 Smoke-testing template '$(TEMPLATE)' via $(TEMPLATE_TUTORIAL) app_files + data"
	$(MAKE) -C $(TEMPLATE_TUTORIAL) run JOB_TYPE=$(TEMPLATE)
