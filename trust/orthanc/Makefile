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

service_name = orthanc
docker_command = docker compose --project-directory .. -f ../deploy/compose_trust.development.yml
run_clean = run --rm --remove-orphans

network1 = deploy_trust-network-1

TRUST_1_VARS = \
	PACS_UI_PORT=${PACS_UI_PORT} \
	ORTHANC_STORAGE_DIR=${ORTHANC_STORAGE_DIR} \
	ORTHANC_USERNAME=${ORTHANC_USERNAME} \
	ORTHANC_PASSWORD=${ORTHANC_PASSWORD} \
	XNAT_PORT=${XNAT_PORT} \
	TRUST_NETWORK_NAME=${network1}

build:
	$(docker_command) build $(service_name)
up-trust-1:
	$(TRUST_1_VARS) $(docker_command) up $(service_name)
down:
	$(TRUST_1_VARS) $(docker_command) down --remove-orphans $(service_name)
shell:
	$(TRUST_1_VARS) $(docker_command) $(run_clean) $(service_name) /bin/bash

# Sync mock orthanc DICOM data (used in development / staging environments where real data is not available)
# Needs AWS account credentials configured to run S3 commands
update-orthanc-data:
	CLEAN_AFTER_UPDATE=True \
	AICENTRE_BUCKET_NAME=$(AICENTRE_BUCKET_NAME) \
	./update_orthanc_data.sh
