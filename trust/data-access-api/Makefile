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
KIT_ENV_FILE=../.env.GSTT.development.example
ifneq ("$(wildcard $(KIT_ENV_FILE))","")
include $(KIT_ENV_FILE)
export $(shell sed 's/=.*//' $(KIT_ENV_FILE))
endif

service_name = data-access-api
lint_command = uv run ruff check . --fix
# Integration tests live under ./tests/integration and bring up a Docker Compose stack
# (omop-db + data-access-api) — they don't run as part of the default unit suite.
test_coverage_html_command = uv run pytest --ignore=./tests/integration --tb=short --disable-warnings --cov=data_access_api/ --cov-report=html:htmlcov-unit --cov-report=term-missing --cov-report=xml:coverage-unit.xml
test_coverage_html_command_integration = uv run pytest ./tests/integration --tb=short --disable-warnings --cov=data_access_api/ --cov-report=html:htmlcov-integration --cov-report=term-missing --cov-report=xml:coverage-integration.xml
docker_command = docker compose --project-directory .. -f ../deploy/compose_trust.development.yml
run_clean = run --rm --remove-orphans
mypy_command = uv run mypy . --ignore-missing-imports

build:
	TRUST_NETWORK_NAME="deploy_trust-network-1" \
	$(docker_command) -p trust1 build $(service_name)
up:
#   For testing, set network to trust 1
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
	BASE_IMAGES_DOWNLOAD_DIR="/tmp/images" \
	TRUST_NETWORK_NAME="deploy_trust-network-1" \
	$(docker_command) $(run_clean) $(service_name) $(lint_command) && $(mypy_command) && $(test_coverage_html_command)
local_lint:
	$(lint_command)
local_mypy:
	$(mypy_command)
local_test:
	BASE_IMAGES_DOWNLOAD_DIR="/tmp/images" \
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command)
unit_test: local_test
integration_test:
	@export BASE_IMAGES_DOWNLOAD_DIR="/tmp/images"; \
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command_integration)
