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

# Pull defaults from the per-trust kit example (the only tracked env source —
# the real kit file is gitignored and may not exist on a dev box).
KIT_ENV_FILE=../.env.Trust_1.example
ifneq ("$(wildcard $(KIT_ENV_FILE))","")
include $(KIT_ENV_FILE)
export $(shell sed 's/=.*//' $(KIT_ENV_FILE))
endif

network1 = deploy_trust-network-1
# network2 = deploy_trust-network-2

service_name = omop-db
docker_command = docker compose --project-directory .. -f ../deploy/compose_trust.development.yml
run_clean = run --rm --remove-orphans

# Using the run command ignores the port mapping
up-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR} \
	OMOP_DB_PORT=${OMOP_DB_PORT} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=Trust_1 \
	$(docker_command) up $(service_name) --pull always

down-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR} \
	OMOP_DB_PORT=${OMOP_DB_PORT} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=Trust_1 \
	$(docker_command) down --remove-orphans $(service_name) -v

shell:
	docker exec -it $$(docker ps -qf "name=$(service_name)") /bin/bash

# Sync mock omop data (used in development / staging environments where real data is not available)
# Fetched anonymously from the public Hugging Face dataset (no AWS CLI / credentials needed)
update-omop-data:
	CLEAN_AFTER_UPDATE=True ./update_omop_data.sh
