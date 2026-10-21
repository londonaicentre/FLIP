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
# Snowflake PLATFORM root (FLIP#1581, epic #1589). THE BREAK-GLASS TIER.
#
# This root creates the account's security objects: the FLIP_<CODE>_* roles and their hierarchy,
# the deploy SERVICE user, the four account privileges the workload role holds, the egress
# integration, the account network/password/session policies, the event table and EVENT_TABLE
# parameter, and the resource monitor. It is applied RARELY, by a PERSON user with MFA holding
# ACCOUNTADMIN (or SECURITYADMIN where that is enough), and never from CI.
#
# Routine work does NOT come here: trust/deploy/terraform/snowflake is the workload root, applied
# as FLIP_<CODE>_DEPLOY_USER with key-pair auth. The two roots keep separate state.
#
# Credentials come from the environment only (SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_PASSCODE
# for username_password_mfa, or SNOWFLAKE_AUTHENTICATOR=externalbrowser), never from a variable.

SHELL := /bin/bash

# PROD only names the state and tfvars files here; there is no per-environment behaviour yet.
include ../../../../deploy/env_mode.mk

# OpenTofu is the supported runtime (state encryption, which this root relies on for local state);
# terraform is accepted as a fallback for sites standardised on it.
TF ?= $(shell command -v tofu 2>/dev/null || command -v terraform 2>/dev/null)

TFVARS ?= terraform.$(ENV).tfvars
STATE_PATH ?= terraform-platform.$(ENV).tfstate
BACKEND_CONFIG ?= path=$(STATE_PATH)
PLAN_FILE := snowflake-platform.tfplan

TFLINT ?= tflint
CHECKOV ?= uvx --from checkov==3.3.14 checkov

define require_tf
	@[ -n "$(TF)" ] || { echo "❌ Neither tofu nor terraform is on PATH."; exit 1; }
endef

define require_tfvars
	@[ -f "$(TFVARS)" ] || { echo "❌ $(TFVARS) not found. Copy terraform.tfvars.example and fill it in."; exit 1; }
endef

# Printed before anything that touches the account. Two confirmations, not one: the first says
# what tier this is, the second shows the plan.
define break_glass_warning
	@echo ""
	@echo "################################################################################"
	@echo "#  BREAK GLASS: Snowflake PLATFORM tier                                        #"
	@echo "################################################################################"
	@echo "#  This root changes the account's SECURITY objects: roles, the deploy user,    #"
	@echo "#  account grants, the egress integration, account network/password/session     #"
	@echo "#  policies, the EVENT_TABLE parameter and the resource monitor.                #"
	@echo "#                                                                               #"
	@echo "#  It is applied RARELY, by a PERSON user with MFA holding ACCOUNTADMIN or      #"
	@echo "#  SECURITYADMIN. It is never run from CI and never run on a schedule.          #"
	@echo "#                                                                               #"
	@echo "#  Routine deploys belong in ../snowflake (the workload root), applied as       #"
	@echo "#  FLIP_<CODE>_DEPLOY_USER with key-pair auth. If you are not deliberately      #"
	@echo "#  changing account security, you are in the wrong directory.                   #"
	@echo "################################################################################"
	@echo ""
	@read -r -p "Type BREAK GLASS to continue: " answer; \
		[ "$$answer" = "BREAK GLASS" ] || { echo "Aborted."; exit 1; }
endef

.PHONY: help init plan apply destroy output lint fmt validate test

help: ## Show this help message
	@echo "FLIP Snowflake PLATFORM (break-glass) infrastructure:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

init: ## Initialise the root (BACKEND_CONFIG=path=... or a site's remote backend settings)
	$(require_tf)
	$(TF) init -input=false -backend-config="$(BACKEND_CONFIG)"

plan: init ## Plan into $(PLAN_FILE)
	$(require_tfvars)
	$(TF) plan -input=false -var-file="$(TFVARS)" -out=$(PLAN_FILE)

apply: ## Warn loudly, confirm twice, show the saved plan, then apply it
	$(require_tf)
	@[ -f "$(PLAN_FILE)" ] || { echo "❌ No $(PLAN_FILE). Run: make plan"; exit 1; }
	$(break_glass_warning)
	$(TF) show $(PLAN_FILE)
	@read -r -p "Apply this plan to the account's security objects? Type yes to continue: " answer; \
		[ "$$answer" = "yes" ] || { echo "Aborted."; exit 1; }
	$(TF) apply $(PLAN_FILE)

destroy: ## Destroy the account's FLIP security objects (asks twice)
	$(require_tf)
	$(require_tfvars)
	$(break_glass_warning)
	$(TF) destroy -var-file="$(TFVARS)"

output: ## Print the names this tier created, as JSON
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
