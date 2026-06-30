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

ROOT_DIR := $(abspath $(CURDIR)/../..)
TESTING_DIR := $(ROOT_DIR)/testing
APP_DIR := $(CURDIR)

# --- Load app-specific env ---
ifneq ("$(wildcard .env.app)","")
include ./.env.app
export $(shell sed 's/=.*//' ./.env.app)
endif

RAW_CHECKPOINT ?=

PROCESSED_CHECKPOINT := $(APP_DIR)/app_files/arkplus_pretrained_weights.pt

.PHONY: run shell down clean

check-raw-checkpoint:
	@if [ -f "$(PROCESSED_CHECKPOINT)" ]; then \
		echo "✅ Processed checkpoint found."; \
	elif [ -n "$(RAW_CHECKPOINT)" ]; then \
		echo "Processing raw checkpoint..."; \
		uv run python "$(APP_DIR)/process_tools/preprocess_checkpoints.py" \
			--entry "$(RAW_CHECKPOINT)" "$(PROCESSED_CHECKPOINT)" arkplus_pretrained; \
	else \
		echo "❌ Missing Ark+ foundation model checkpoint."; \
		echo ""; \
		echo "Download Ark6_swinLarge768_ep50.pth.tar from:"; \
		echo "  https://www.dropbox.com/scl/fo/joycn8m93nvlrc8yjme40/ABBtPc5oaalYZ7kzmERpjhU/Ark%2B_Nature/Ark6_swinLarge768_ep50.pth.tar?rlkey=p3lphqvtgmiphw1n0u039jpjn&dl=1"; \
		echo "  Request access: https://forms.gle/qkoDGXNiKRPTDdCe8"; \
		echo ""; \
		echo "Then run: make run RAW_CHECKPOINT=/path/to/Ark6_swinLarge768_ep50.pth.tar"; \
		exit 1; \
	fi

run: check-raw-checkpoint
	$(MAKE) -C $(TESTING_DIR) $@ \
		JOB_TYPE="$(JOB_TYPE)" \
		PATH_TO_APP="$(APP_DIR)" \
		DEV_IMAGES_DIR="$(DEV_IMAGES_DIR)" \
		DEV_DATAFRAME="$(DEV_DATAFRAME)" \
		SITE1_IMAGES_DIR="$(SITE1_IMAGES_DIR)" \
		SITE1_DATAFRAME="$(SITE1_DATAFRAME)" \
		SITE2_IMAGES_DIR="$(SITE2_IMAGES_DIR)" \
		SITE2_DATAFRAME="$(SITE2_DATAFRAME)"

shell:
	$(MAKE) -C $(TESTING_DIR) $@ \
		JOB_TYPE="$(JOB_TYPE)" \
		PATH_TO_APP="$(APP_DIR)" \
		DEV_IMAGES_DIR="$(DEV_IMAGES_DIR)" \
		DEV_DATAFRAME="$(DEV_DATAFRAME)" \
		SITE1_IMAGES_DIR="$(SITE1_IMAGES_DIR)" \
		SITE1_DATAFRAME="$(SITE1_DATAFRAME)" \
		SITE2_IMAGES_DIR="$(SITE2_IMAGES_DIR)" \
		SITE2_DATAFRAME="$(SITE2_DATAFRAME)"
down:
	$(MAKE) -C $(TESTING_DIR) $@

clean:
	$(MAKE) -C $(TESTING_DIR) $@
