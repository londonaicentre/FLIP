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

# GitHub Actions OIDC roles for the Terraform pipeline (FLIP#962).
#
# Applied from a laptop, never by CI. That is the design, not an oversight: the
# roles CI assumes must not be managed by CI, or a bad apply locks the pipeline
# out of the apply that would fix it. See backend.tf.
#
#     make init && make plan && make apply             # stag account
#     make init PROD=true && make plan PROD=true ...   # prod account

# DRIFT_BRANCH is the branch terraform_drift.yml is *loaded from*, and is a
# separate axis from APPLY_BRANCH even where the two happen to agree.
#
#   stag  develop — the schedule fires on the repository default branch.
#   prod  main    — the scheduled run on the default branch re-dispatches the
#                   workflow onto main (`gh workflow run … --ref main`), so the
#                   aws-prod environment does not have to admit develop and hand
#                   the production secrets to every workflow on it.
#
# Deriving this from APPLY_BRANCH is what left the production drift job unable to
# assume the plan role: it presented `terraform_drift.yml@refs/heads/develop`
# against a policy trusting `@refs/heads/main`.
ifeq ($(PROD),true)
MAIN_ENV_FILE = ../../../../.env.production
TF_ENVIRONMENT = prod
APPLY_BRANCH = main
DRIFT_BRANCH = main
GITHUB_ENVIRONMENT = aws-prod
else
PROD = stag
MAIN_ENV_FILE = ../../../../.env.stag
TF_ENVIRONMENT = stag
APPLY_BRANCH = develop
DRIFT_BRANCH = develop
GITHUB_ENVIRONMENT = aws-stag
endif

ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(MAIN_ENV_FILE))
endif

# Same credential hygiene as the parent root: static AWS_* env vars take priority
# over the SSO chain in the AWS Go SDK and produce InvalidClientTokenId errors
# when .env carries empty or literal-quoted values for the omop-db init job.
_TF := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; terraform

PROD_AWS_PROFILE ?= prod
STAG_AWS_PROFILE ?= stag

# Refuse to create prod's roles in the stag account, or the reverse. Profile name
# is the guard because make cannot cheaply read the account ID — the `account_id`
# output exists so the result can be checked after the fact.
ifeq ($(PROD),true)
ifneq ($(AWS_PROFILE),$(PROD_AWS_PROFILE))
$(error For the prod CI roles, AWS_PROFILE must be '$(PROD_AWS_PROFILE)' (override via PROD_AWS_PROFILE))
endif
else
ifneq ($(AWS_PROFILE),$(STAG_AWS_PROFILE))
$(error For the stag CI roles, AWS_PROFILE must be '$(STAG_AWS_PROFILE)' (override via STAG_AWS_PROFILE))
endif
endif

ifeq ($(strip $(FLIP_TFSTATE_BUCKET_NAME)),)
$(error Set FLIP_TFSTATE_BUCKET_NAME in $(MAIN_ENV_FILE))
endif

export TF_VAR_AWS_REGION = $(AWS_REGION)
export TF_VAR_environment = $(TF_ENVIRONMENT)
export TF_VAR_apply_branch = $(APPLY_BRANCH)
export TF_VAR_drift_branch = $(DRIFT_BRANCH)
export TF_VAR_github_environment = $(GITHUB_ENVIRONMENT)
export TF_VAR_state_bucket_name = $(FLIP_TFSTATE_BUCKET_NAME)

.PHONY: init
init: ## Initialize the CI-roles Terraform root
	@echo "🔧 Initializing CI roles for '$(TF_ENVIRONMENT)' (profile $(AWS_PROFILE))..."
	@$(_TF) init \
		-backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" \
		-backend-config="region=$(AWS_REGION)" \
		-reconfigure

.PHONY: plan
plan: ## Plan the CI roles
	@echo "📋 Planning CI roles for '$(TF_ENVIRONMENT)'..."
	@$(_TF) plan -out=plan.tfplan

.PHONY: apply
apply: ## Apply the CI roles
	@echo "🛠️  Applying CI roles for '$(TF_ENVIRONMENT)'..."
	@$(_TF) apply plan.tfplan
	@echo ""
	@echo "Wire these into the GitHub environment '$(GITHUB_ENVIRONMENT)' as variables:"
	@$(_TF) output

.PHONY: output
output: ## Print the role ARNs and the OIDC claims they expect
	@$(_TF) output

.PHONY: help
help: ## Show this help message
	@echo "FLIP Terraform CI roles:"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
