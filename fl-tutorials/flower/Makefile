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
#   make download-xray-data                    # FLIP-format x-ray data from HF
#   make download-spleen-data                  # FLIP-format MSD-spleen data from HF
#   make list-tutorials
#   make run-tutorial TUTORIAL=numpy           # self-contained, no data needed
#   make run-tutorial TUTORIAL=3d_spleen_segmentation   # needs download-spleen-data
#   make run-all-tutorials
#
# Datasets land (gitignored) under data/ in FLIP accession-resources layout, the
# same Hugging Face source the NVFLARE tutorials use.

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HF_DATA_REPO := aicentreflip/flip-fl-base-test-data

# Tutorials = immediate subdirs carrying an app/ (numpy, xray_classification,
# 3d_spleen_segmentation, 3d_spleen_segmentation_evaluation).
TUTORIAL_DIRS := $(sort $(patsubst %/app/,%,$(dir $(wildcard */app/.))))

.PHONY: list-tutorials run-tutorial run-all-tutorials download-xray-data download-spleen-data

# Download the FLIP-format x-ray dataset from Hugging Face into data/xrays_mini_300/
# (gitignored). Idempotent. Mirrors fl-tutorials/nvflare's download-xray-data.
download-xray-data:
	@echo "⬇️  Downloading x-ray dataset from Hugging Face ($(HF_DATA_REPO))..."
	@uv run --no-project --with huggingface_hub python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$(HF_DATA_REPO)', repo_type='dataset', local_dir='data/.hf', allow_patterns=['flip-fl-base-test-data/xrays_mini_300/*'])"
	@rm -rf data/xrays_mini_300 && mkdir -p data/xrays_mini_300
	@cp -r data/.hf/flip-fl-base-test-data/xrays_mini_300/accession-resources data/xrays_mini_300/accession-resources
	@cp data/.hf/flip-fl-base-test-data/xrays_mini_300/sample_get_dataframe_response.csv data/xrays_mini_300/sample_get_dataframe_response.csv
	@echo "✅ x-ray data ready at data/xrays_mini_300/ — make run-tutorial TUTORIAL=xray_classification"

# Download the FLIP-format spleen dataset (accession-resources + dataframe) from HF
# into data/spleen/ (gitignored), shared by the segmentation + evaluation tutorials.
download-spleen-data:
	@echo "⬇️  Downloading spleen dataset from Hugging Face ($(HF_DATA_REPO))..."
	@uv run --no-project --with huggingface_hub python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='$(HF_DATA_REPO)', repo_type='dataset', local_dir='data/.hf', allow_patterns=['flip-fl-base-test-data/spleen/*', 'flip-fl-base-test-data/checkpoints/*'])"
	@rm -rf data/spleen && mkdir -p data/spleen
	@cp -r data/.hf/flip-fl-base-test-data/spleen/accession-resources data/spleen/accession-resources
	@cp data/.hf/flip-fl-base-test-data/spleen/sample_get_dataframe_response.csv data/spleen/sample_get_dataframe_response.csv
	@mkdir -p data/model_checkpoints && cp data/.hf/flip-fl-base-test-data/checkpoints/model.pt data/model_checkpoints/model.pt
	@echo "✅ spleen data ready at data/spleen/ (+ checkpoint) — make run-tutorial TUTORIAL=3d_spleen_segmentation"

list-tutorials:
	@echo "Available Flower tutorials (pass the name to 'make run-tutorial TUTORIAL=…'):"
	@for d in $(TUTORIAL_DIRS); do echo "  - $$d"; done

# Run one tutorial on the standalone Flower stack (up → submit → wait → down).
# numpy is self-contained; spleen/xray need 'make download-<spleen|xray>-data' first.
run-tutorial:
	@bash $(REPO_ROOT)/fl-tutorials/flower/run-tutorial.sh "$(TUTORIAL)"

run-all-tutorials:
	@for d in $(TUTORIAL_DIRS); do \
		bash $(REPO_ROOT)/fl-tutorials/flower/run-tutorial.sh "$$d" || { echo "❌ tutorial $$d failed"; exit 1; }; \
	done
