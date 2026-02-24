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

# Load environment variables for development
ifneq ("$(wildcard ../.env.development)","")
include ../.env.development
export $(shell sed 's/=.*//' ../.env.development)
endif
# Load GITHUB secrets if they exist
ifneq ("$(wildcard ../.env.github)","")
include ../.env.github
export $(shell sed 's/=.*//' ../.env.github)
endif

service_name = flip-api
lint_command = uv run ruff check . --fix
test_args = --tb=short --disable-warnings --cov=src/ --cov-report=html --cov-report=term-missing
test_coverage_html_command = uv run pytest $(test_args)
test_coverage_html_command_unit = uv run pytest ./tests/unit ./tests/step_functions_services $(test_args)
test_coverage_html_command_integration = uv run pytest ./tests/integration $(test_args)
docker_command = DEBUG=$(DEBUG) docker compose -f ../deploy/compose.development.yml
run_clean = run --rm --remove-orphans
mypy_command = uv run mypy . --ignore-missing-imports

up:
# Note we need to start the db first
	@echo "🚀 Starting flip-db..."
	$(docker_command) up --remove-orphans -d flip-db
	@echo "🚀 Starting $(service_name)..."
	$(docker_command) up --remove-orphans $(service_name)
down:
# Also kill the db
	@echo "🛑 Stopping $(service_name) ..."
	$(docker_command) down --remove-orphans $(service_name)
	@echo "🛑 Stopping flip-db ..."
	$(docker_command) down --remove-orphans flip-db
shell:
	$(docker_command) $(run_clean) $(service_name) /bin/bash
lint:
	$(docker_command) $(run_clean) $(service_name) $(lint_command)
mypy:
	$(docker_command) $(run_clean) $(service_name) $(mypy_command)
test:
	$(docker_command) $(run_clean) $(service_name) $(lint_command) && $(mypy_command) && $(test_coverage_html_command)
local_lint:
	$(lint_command)
local_mypy:
	$(mypy_command)
local_test:
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command) --skip-client --skip-db
unit_test:
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command_unit) --skip-client --skip-db
integration_test:
	$(lint_command) && $(mypy_command) && $(test_coverage_html_command_integration)
build:
	$(docker_command) build $(service_name)
restart: down up
debug:
	@echo "🚨 Starting debug mode..."
	@echo "🚨 Setting DEBUG=true"
	$(docker_command) restart --remove-orphans $(service_name)
create_testing_projects:
	@echo "🔢 Creating projects for debugging..."
	env DB_HOST=localhost uv run python -m tests.debug_prelaunch_task
delete_testing_projects:
	@echo "🔢 Removing projects created for debugging..."
	env DB_HOST=localhost uv run python -m tests.debug_post_debug_task
