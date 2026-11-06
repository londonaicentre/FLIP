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
# Credential-free checks for the AzureTRE workspace service bundle (FLIP#1792). Building and
# registering it happen in an AzureTRE checkout: see README.md.

TF := terraform
RUFF := uvx ruff@0.14.7

.PHONY: help test lint

help: ## Show this help message
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-6s %s\n", $$1, $$2}'

test: ## terraform validate and test (mocked Azure, no backend), then pytest
	$(TF) -chdir=terraform init -backend=false -input=false >/dev/null
	$(TF) -chdir=terraform validate -no-color
	$(TF) -chdir=terraform test -no-color
	uv run --no-project --with pytest --with pyyaml pytest tests/ -q

lint: ## terraform fmt check, ruff check and format check
	$(TF) fmt -check -recursive
	$(RUFF) check tests
	$(RUFF) format --check tests
