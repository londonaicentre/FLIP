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

.PHONY: run shell down clean

run:
	$(MAKE) -C $(TESTING_DIR) $@ \
		JOB_TYPE="$(JOB_TYPE)" \
		PATH_TO_APP="$(APP_DIR)" \
		DEV_IMAGES_DIR="$(DEV_IMAGES_DIR)" \
		DEV_DATAFRAME="$(DEV_DATAFRAME)"

shell:
	$(MAKE) -C $(TESTING_DIR) $@ \
		JOB_TYPE="$(JOB_TYPE)" \
		PATH_TO_APP="$(APP_DIR)" \
		DEV_IMAGES_DIR="$(DEV_IMAGES_DIR)" \
		DEV_DATAFRAME="$(DEV_DATAFRAME)"

down:
	$(MAKE) -C $(TESTING_DIR) $@

clean:
	$(MAKE) -C $(TESTING_DIR) $@
