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
# Snowflake trust node infrastructure, WORKLOAD tier (FLIP#1581, epic #1589).
#
# Applied routinely as FLIP_<CODE>_DEPLOY_USER (TYPE = SERVICE, key-pair auth). It creates the
# trust database and its schemas, the warehouse, the compute pools, the image repository, the SSE
# stages, the nightly pool-suspend task, and the grants on those objects to the service, OMOP and
# observer roles. Nothing here is an account-level security object.
#
# The roles themselves, the deploy user, the account grants, the egress integration, the account
# policies, the event table and the resource monitor belong to the break-glass tier next door,
# ../snowflake-platform, applied rarely by a person user with MFA. The two roots keep separate
# state, so a routine apply cannot reach an account security object even by mistake.
#
# The SPCS service specs and their lifecycle targets live in trust/deploy/snowflake (FLIP#1582),
# and the kit file is the only coupling to a hub. No hub state, no hub credentials.
#
# Credentials come from the environment (SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
# SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT) or ~/.snowflake/connections.toml, never from a variable.
# No target skips the confirmation prompt: apply and destroy both ask.

SHELL := /bin/bash

# PROD only names the state and tfvars files here; there is no per-environment behaviour yet.
include ../../../../deploy/env_mode.mk

# OpenTofu is the supported runtime (state encryption, which this root relies on for local state);
# terraform is accepted as a fallback for sites standardised on it.
TF ?= $(shell command -v tofu 2>/dev/null || command -v terraform 2>/dev/null)

TFVARS ?= terraform.$(ENV).tfvars
STATE_PATH ?= terraform.$(ENV).tfstate
BACKEND_CONFIG ?= path=$(STATE_PATH)
PLAN_FILE := snowflake.tfplan

TFLINT ?= tflint
CHECKOV ?= uvx --from checkov==3.3.14 checkov

define require_tf
	@[ -n "$(TF)" ] || { echo "❌ Neither tofu nor terraform is on PATH."; exit 1; }
endef

define require_tfvars
	@[ -f "$(TFVARS)" ] || { echo "❌ $(TFVARS) not found. Copy terraform.tfvars.example and fill it in."; exit 1; }
endef

.PHONY: help init plan apply destroy output lint fmt validate test

help: ## Show this help message
	@echo "FLIP Snowflake trust infrastructure (workload tier):"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init: ## Initialise the root (BACKEND_CONFIG=path=... or a site's remote backend settings)
	$(require_tf)
	$(TF) init -input=false -backend-config="$(BACKEND_CONFIG)"

plan: init ## Plan into $(PLAN_FILE)
	$(require_tfvars)
	$(TF) plan -input=false -var-file="$(TFVARS)" -out=$(PLAN_FILE)

apply: ## Show the saved plan, ask, then apply it
	$(require_tf)
	@[ -f "$(PLAN_FILE)" ] || { echo "❌ No $(PLAN_FILE). Run: make plan"; exit 1; }
	$(TF) show $(PLAN_FILE)
	@read -r -p "Apply this plan to Snowflake? Type yes to continue: " answer; \
		[ "$$answer" = "yes" ] || { echo "Aborted."; exit 1; }
	$(TF) apply $(PLAN_FILE)

destroy: ## Destroy every object this root created (asks first)
	$(require_tf)
	$(require_tfvars)
	$(TF) destroy -var-file="$(TFVARS)"

output: ## Print the outputs #1582 consumes, as JSON
	$(require_tf)
	$(TF) output -json

fmt: ## Check formatting
	$(require_tf)
	$(TF) fmt -check -recursive -diff

validate: ## Validate without a backend or credentials
	$(require_tf)
	$(TF) init -backend=false -input=false
	$(TF) validate

lint: fmt validate ## The static checks CI runs: fmt, validate, tflint, checkov
	$(TFLINT) --chdir=. --config="$(CURDIR)/.tflint.hcl" --init
	$(TFLINT) --chdir=. --config="$(CURDIR)/.tflint.hcl"
	$(CHECKOV) --directory . --framework terraform --quiet --compact

test: ## Run the credential-free static test suite
	uv run --no-project --with pytest pytest tests/ -v
