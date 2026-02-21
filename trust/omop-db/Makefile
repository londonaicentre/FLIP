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

# if tag PROD=true is passed, set PROD=true
ifeq ($(PROD),true)
MAIN_ENV_FILE=../../.env.production
else ifeq ($(PROD),stag)
MAIN_ENV_FILE=../../.env.stag
else
MAIN_ENV_FILE=../../.env.development
endif

# replace environment variables by the values from the .env files
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

network1 = deploy_trust-network-1
# network2 = deploy_trust-network-2

service_name = omop-db
docker_command = docker compose -f ../compose_trust.development.yml
run_clean = run --rm --remove-orphans

# Using the run command ignores the port mapping
up-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST1} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_1} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=trust1 \
	$(docker_command) up $(service_name) --pull always

down-test-omop-trust1:
# 	This will just be used for testing
	BASE_IMAGES_DOWNLOAD_DIR=${BASE_IMAGES_DOWNLOAD_DIR_TRUST1} \
	OMOP_DB_PORT=${OMOP_DB_PORT_TRUST_1} \
	TRUST_NETWORK_NAME=${network1} \
	TRUST_NAME=trust1 \
	$(docker_command) down --remove-orphans $(service_name) -v

shell:
	docker exec -it $$(docker ps -qf "name=$(service_name)") /bin/bash

# Sync mock omop data (used in development / staging environments where real data is not available)
# Needs AWS account credentials configured to run S3 commands
update-omop-data:
	CLEAN_AFTER_UPDATE=True AICENTRE_BUCKET_NAME=$(AICENTRE_BUCKET_NAME) ./update_omop_data.sh


# ================ Legacy commands -- to be removed eventually -- use update-omop-data above ================
# NOTE These hardcode a specific date for the omop data tarballs, and will need to be updated when new data is available

# Sync data from S3 bucket (mainly used temporarily in production)
# Needs AWS account credentials configured
sync-data-from-s3:
	aws s3 sync s3://$(AICENTRE_BUCKET_NAME)/omop/ ./volumes --exclude "*" --include "trust*_pgdata_20260122.tar.gz"

extract-data: remove-data
	mkdir -p ./volumes/trust1/db_data ./volumes/trust2/db_data
	tar -xzf ./volumes/trust1_pgdata_20260122.tar.gz -C ./volumes/trust1/db_data
	tar -xzf ./volumes/trust2_pgdata_20260122.tar.gz -C ./volumes/trust2/db_data
	rm ./volumes/trust1_pgdata_20260122.tar.gz ./volumes/trust2_pgdata_20260122.tar.gz

remove-data:
	sudo rm -rf ./volumes/trust1/db_data ./volumes/trust2/db_data
