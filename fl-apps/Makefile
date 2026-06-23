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

# FL app template and tutorial targets

SCRIPT_DIR := scripts
MERGED_DIR ?= .test_runs/merged-job-dir
TEST_DATA_DIR = .test_data/flip-fl-base-test-data

# Test environment variables for xrays tests
TEST_XRAYS_VARS = \
	DEV_IMAGES_DIR=../$(TEST_DATA_DIR)/xrays_mini_300/accession-resources \
	DEV_DATAFRAME=../$(TEST_DATA_DIR)/xrays_mini_300/sample_get_dataframe_response.csv \
	RUNS_DIR=../.test_runs/xrays_mini_300

# Test environment variables for spleen tests
TEST_SPLEEN_VARS = \
	DEV_IMAGES_DIR=../$(TEST_DATA_DIR)/spleen/accession-resources \
	DEV_DATAFRAME=../$(TEST_DATA_DIR)/spleen/sample_get_dataframe_response.csv \
	RUNS_DIR=../.test_runs/spleen

download-test-data:
	@if [ ! -d ".test_data" ]; then \
		mkdir -p .test_data && \
		uv run python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='aicentreflip/flip-fl-base-test-data', repo_type='dataset', local_dir='.test_data')" \
	else \
		echo "Directory .test_data already exists, skipping download."; \
	fi

test-xrays-standard:
	@$(SCRIPT_DIR)/merge-job-dirs.sh templates/standard/app tutorials/image_classification/xray_classification/app_files "$(MERGED_DIR)"
	$(TEST_XRAYS_VARS) JOB_DIR="../$(MERGED_DIR)" $(COMPOSE_TEST_CMD) nvflare-simulator-test

test-spleen-standard:
	@$(SCRIPT_DIR)/merge-job-dirs.sh templates/standard/app tutorials/image_segmentation/3d_spleen_segmentation/app_files "$(MERGED_DIR)"
	$(TEST_SPLEEN_VARS) JOB_DIR="../$(MERGED_DIR)" $(COMPOSE_TEST_CMD) nvflare-simulator-test

test-spleen-evaluation:
	@$(SCRIPT_DIR)/merge-job-dirs.sh templates/evaluation/app tutorials/image_evaluation/3d_spleen_segmentation_evaluation/app_files "$(MERGED_DIR)"
	@cp -v $(TEST_DATA_DIR)/checkpoints/model.pt "$(MERGED_DIR)/custom/model.pt"
	$(TEST_SPLEEN_VARS) JOB_DIR="../$(MERGED_DIR)" $(COMPOSE_TEST_CMD) nvflare-simulator-test

test-spleen-diffusion:
	@$(SCRIPT_DIR)/merge-job-dirs.sh templates/diffusion_model/app tutorials/image_synthesis/latent_diffusion_model/app_files "$(MERGED_DIR)"
	$(TEST_SPLEEN_VARS) JOB_DIR="../$(MERGED_DIR)" $(COMPOSE_TEST_CMD) nvflare-simulator-test

test-spleen-fedopt:
	@$(SCRIPT_DIR)/merge-job-dirs.sh templates/fed_opt/app tutorials/image_segmentation/3d_spleen_segmentation/app_files "$(MERGED_DIR)"
	$(TEST_SPLEEN_VARS) JOB_DIR="../$(MERGED_DIR)" $(COMPOSE_TEST_CMD) nvflare-simulator-test

test:
	@echo "Running integration tests with filtered output (showing only errors, warnings, and test results)..."
	@echo "============================== XRays Standard Test =============================="
	$(MAKE) test-xrays-standard 2>&1 | grep -i -A5 -B5 "make\[1\]: Leaving\|exited with code\|ERROR\|FAILED"
	@echo "============================== Spleen Standard Test =============================="
	$(MAKE) test-spleen-standard 2>&1 | grep -i -A5 -B5 "make\[1\]: Leaving\|exited with code\|ERROR\|FAILED"
	@echo "============================== Spleen Evaluation Test =============================="
	$(MAKE) test-spleen-evaluation 2>&1 | grep -i -A5 -B5 "make\[1\]: Leaving\|exited with code\|ERROR\|FAILED"
	@echo "============================== Spleen Diffusion Test =============================="
	$(MAKE) test-spleen-diffusion 2>&1 | grep -i -A5 -B5 "make\[1\]: Leaving\|exited with code\|ERROR\|FAILED"

.PHONY: download-test-data test-xrays-standard test-spleen-standard test-spleen-evaluation \
        test-spleen-diffusion test-spleen-fedopt test
