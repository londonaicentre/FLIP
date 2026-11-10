# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# Snowflake IDENTITY root (FLIP#1581, epic #1589).
#
# The six FLIP_<CODE>_* roles, their hierarchy and the deploy SERVICE user. Applied ONCE, before
# the platform root, by whoever administers users in the account, as a role that can create roles
# and users. It holds its own state, so neither of the other roots can touch an identity.
#
# Credentials come from the environment only, never from a variable.

SHELL := /bin/bash

# HUB only names the state and tfvars files here; there is no per-environment behaviour.
include ../../../../deploy/env_mode.mk

# OpenTofu is the supported runtime (state encryption, which this root relies on for local state);
# terraform is accepted as a fallback for sites standardised on it.
TF ?= $(shell command -v tofu 2>/dev/null || command -v terraform 2>/dev/null)

TFVARS ?= terraform.$(ENV).tfvars
STATE_PATH ?= terraform-identity.$(ENV).tfstate
BACKEND_CONFIG ?= path=$(STATE_PATH)
PLAN_FILE := snowflake-identity.tfplan

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
	@echo "FLIP Snowflake IDENTITY infrastructure (roles and the deploy user):"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init: ## Initialise the root (BACKEND_CONFIG=path=... or a site's remote backend settings)
	$(require_tf)
	$(TF) init -input=false -backend-config="$(BACKEND_CONFIG)"

plan: init ## Plan into $(PLAN_FILE)
	$(require_tfvars)
	$(TF) plan -input=false -var-file="$(TFVARS)" -out=$(PLAN_FILE)

apply: ## Show the saved plan, confirm, then apply it
	$(require_tf)
	@[ -f "$(PLAN_FILE)" ] || { echo "❌ No $(PLAN_FILE). Run: make plan"; exit 1; }
	$(TF) show $(PLAN_FILE)
	@read -r -p "Create these roles and the deploy user in the account? Type yes to continue: " answer; \
		[ "$$answer" = "yes" ] || { echo "Aborted."; exit 1; }
	$(TF) apply $(PLAN_FILE)

destroy: ## Drop the FLIP roles and the deploy user (asks; destroy the other two roots first)
	$(require_tf)
	$(require_tfvars)
	$(TF) destroy -var-file="$(TFVARS)"

output: ## Print the names this root created, as JSON
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
