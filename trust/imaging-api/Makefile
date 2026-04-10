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

service_name = imaging-api
lint_command = uv run ruff check . --fix
test_coverage_html_command = uv run pytest --tb=short --disable-warnings --cov=imaging_api/ --cov-report=html --cov-report=term-missing --cov-report=xml:coverage.xml
docker_command = docker compose -f ../compose_trust.development.yml
run_clean = run --rm --remove-orphans
mypy_command = uv run mypy . --ignore-missing-imports

build:
	TRUST_NETWORK_NAME="deploy_trust-network-1" \
	$(docker_command) -p trust1 build $(service_name)
up:
# 	Set the XNAT_PORT to the trust 1 value
	@export XNAT_PORT=8104; \
	export BASE_IMAGES_DOWNLOAD_DIR=/tmp/images; \
	TRUST_NETWORK_NAME="deploy_trust-network-1" \
	$(docker_command) up $(service_name)
down:
	$(docker_command) down --remove-orphans $(service_name)
shell:
	$(docker_command) $(run_clean) $(service_name) /bin/bash
lint:
	$(docker_command) $(run_clean) $(service_name) $(lint_command)
mypy:
	$(docker_command) $(run_clean) $(service_name) $(mypy_command)
test:
# 	Just set the XNAT_PORT to a dummy value for the tests to run
	@export XNAT_PORT=1111; \
	export BASE_IMAGES_DOWNLOAD_DIR=/tmp/images; \
	TRUST_NETWORK_NAME="deploy_trust-network-1" \
	$(docker_command) $(run_clean) $(service_name) $(lint_command) && $(mypy_command) && $(test_coverage_html_command)
local_lint:
	$(lint_command)
local_mypy:
	$(mypy_command)
local_test:
# 	Just set the XNAT_PORT to a dummy value for the tests to run
	@export XNAT_PORT=1111; \
	export BASE_IMAGES_DOWNLOAD_DIR=/tmp/images; \
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command)
unit_test: local_test
