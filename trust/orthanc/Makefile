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

ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

service_name = orthanc
docker_command = docker compose -f ../compose_trust.yml
run_clean = run --rm --remove-orphans

network1 = deploy_trust-network-1
network2 = deploy_trust-network-2

TRUST_1_VARS = \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_1} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_1} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_1} \
	TRUST_NETWORK_NAME=${network1}
TRUST_2_VARS = \
	PACS_UI_PORT=${PACS_UI_PORT_TRUST_2} \
	ORTHANC_STORAGE=${ORTHANC_STORAGE_TRUST_2} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT_TRUST_2} \
	TRUST_NETWORK_NAME=${network2}

build:
	$(docker_command) build $(service_name)
up-trust-1:
	$(TRUST_1_VARS) $(docker_command) up $(service_name)
up-trust-2:
	$(TRUST_2_VARS) $(docker_command) up $(service_name)
down:
	$(TRUST_1_VARS) $(docker_command) down --remove-orphans $(service_name)
shell:
	$(TRUST_1_VARS) $(docker_command) $(run_clean) $(service_name) /bin/bash

# Sync data from S3 bucket (mainly used temporarily in production)
# Needs AWS account credentials configured
sync-data-from-s3:
	aws s3 sync s3://$(AICENTRE_BUCKET_NAME)/orthanc/ ./volumes

extract-data:
	mkdir -p ./${ORTHANC_STORAGE_TRUST_1} ./${ORTHANC_STORAGE_TRUST_2}
	tar -xzf ./volumes/trust1_orthanc_data_20260106.tar.gz -C ./${ORTHANC_STORAGE_TRUST_1}
	tar -xzf ./volumes/trust2_orthanc_data_20260106.tar.gz -C ./${ORTHANC_STORAGE_TRUST_2}
	rm ./volumes/trust1_orthanc_data_20260106.tar.gz ./volumes/trust2_orthanc_data_20260106.tar.gz
