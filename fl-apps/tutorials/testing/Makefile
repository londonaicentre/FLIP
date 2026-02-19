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

export UID := $(shell id -u)
export GID := $(shell id -g)
export UNAME := ${USER}

# Load environment variables from .env.development file
ifneq ("$(wildcard .env.testing)","")
include ./.env.testing
export $(shell sed 's/=.*//' ./.env.testing)
endif

service_name = nvflare-simulator
docker_command = docker compose -f compose.yml

prep: clean checks
	bash ./app_organiser.sh ${REPO_BRANCH} ${REPO_URL} ${JOB_TYPE} ${PATH_TO_APP}

up:
	$(docker_command) run --rm --remove-orphans $(service_name)

down:
	$(docker_command) down --remove-orphans $(service_name)

clean:
	bash ./cleanup.sh

run: prep up down clean

shell:
	$(docker_command) run --rm --entrypoint "/bin/bash" $(service_name)

checks:
# 	Check FL_BASE_IMAGE_TAG is set
	@if [ -z "${FL_BASE_IMAGE_TAG}" ]; then \
		echo "❌ ERROR: FL_BASE_IMAGE_TAG not set."; \
		echo "   Make sure you have set FL_BASE_IMAGE_TAG in .env.testing"; \
		exit 1; \
	fi
	@echo "✅ Using FL_BASE_IMAGE_TAG: ${FL_BASE_IMAGE_TAG}"

# 	Check PATH_TO_APP and JOB_TYPE are set
	@if [ -z "${PATH_TO_APP}" ] || [ -z "${JOB_TYPE}" ]; then \
		echo "❌ ERROR: PATH_TO_APP and/or JOB_TYPE not set."; \
		echo "   Make sure you are running from an app folder with a valid .env.app"; \
		exit 1; \
	fi
	@echo "✅ Using PATH_TO_APP: ${PATH_TO_APP}"
	@echo "✅ Using JOB_TYPE: ${JOB_TYPE}"

# 	Check DEV_IMAGES_DIR and DEV_DATAFRAME are set
	@if [ -z "${DEV_IMAGES_DIR}" ] || [ -z "${DEV_DATAFRAME}" ]; then \
		echo "❌ ERROR: DEV_IMAGES_DIR and/or DEV_DATAFRAME not set."; \
		echo "   Make sure you are running from an app folder with a valid .env.app"; \
		exit 1; \
	fi
	@echo "✅ Using DEV_IMAGES_DIR: ${DEV_IMAGES_DIR}"
	@echo "✅ Using DEV_DATAFRAME: ${DEV_DATAFRAME}"

# 	Check REPO_URL and REPO_BRANCH are set
	@if [ -z "${REPO_URL}" ] || [ -z "${REPO_BRANCH}" ]; then \
		echo "❌ ERROR: REPO_URL and/or REPO_BRANCH not set."; \
		echo "   Make sure you have set these in .env.testing"; \
		exit 1; \
	fi
	@echo "✅ Using REPO_URL: ${REPO_URL}"
	@echo "✅ Using REPO_BRANCH: ${REPO_BRANCH}"

# 	Check NUM_CLIENTS is set
	@if [ -z "${NUM_CLIENTS}" ]; then \
		echo "❌ ERROR: NUM_CLIENTS not set."; \
		echo "   Make sure you have set NUM_CLIENTS in .env.testing"; \
		exit 1; \
	fi
	@echo "✅ Using NUM_CLIENTS: ${NUM_CLIENTS}"
