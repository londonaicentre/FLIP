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
#
# PROD is deploy/env_mode.mk's token, so this root speaks the same language as the
# rest of the repository. The four values, and what each selects here:
#
#   PROD      profile    env file            env class  branches        GitHub env
#   stag      stag       .env.stag           stag       develop         aws-stag
#   true      prod       .env.production     prod       main            aws-prod
#   lza-stag  lza-stag   .env.lza-stag       stag       develop         aws-stag
#   lza       lza-prod   .env.lza-prod       prod       main            aws-prod
#
# The LZA pair shares the two GitHub environments with the legacy pair: FLIP's
# estate was repointed at the LZA accounts rather than given new environment
# names, so `github_environment` — half of the OIDC trust policy — is unchanged
# and only the account, the env file and the local profile differ (see
# ../README.md, "Repointing CI at the LZA accounts"). The env class is what the
# rest of the root branches on, which is why PROD=lza is prod-grade.
ifeq ($(strip $(PROD)),)
PROD = stag
endif

ifeq ($(PROD),stag)
MAIN_ENV_FILE = ../../../../.env.stag
TF_ENVIRONMENT = stag
APPLY_BRANCH = develop
DRIFT_BRANCH = develop
GITHUB_ENVIRONMENT = aws-stag
EXPECTED_AWS_PROFILE = $(STAG_AWS_PROFILE)
else ifeq ($(PROD),true)
MAIN_ENV_FILE = ../../../../.env.production
TF_ENVIRONMENT = prod
APPLY_BRANCH = main
DRIFT_BRANCH = main
GITHUB_ENVIRONMENT = aws-prod
EXPECTED_AWS_PROFILE = $(PROD_AWS_PROFILE)
else ifeq ($(PROD),lza-stag)
MAIN_ENV_FILE = ../../../../.env.lza-stag
TF_ENVIRONMENT = stag
APPLY_BRANCH = develop
DRIFT_BRANCH = develop
GITHUB_ENVIRONMENT = aws-stag
EXPECTED_AWS_PROFILE = $(LZA_STAG_AWS_PROFILE)
else ifeq ($(PROD),lza)
MAIN_ENV_FILE = ../../../../.env.lza-prod
TF_ENVIRONMENT = prod
APPLY_BRANCH = main
DRIFT_BRANCH = main
GITHUB_ENVIRONMENT = aws-prod
EXPECTED_AWS_PROFILE = $(LZA_AWS_PROFILE)
else
$(error PROD must be unset, stag, true, lza or lza-stag — got '$(PROD)')
endif

ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(MAIN_ENV_FILE))
endif

# Same credential hygiene as the parent root: static AWS_* env vars take priority
# over the SSO chain in the AWS Go SDK and produce InvalidClientTokenId errors
# when .env carries empty or literal-quoted values for the omop-db init job.
_TF := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; terraform

# One profile knob per account, named exactly as the root Makefile names them, so
# an operator sets the same variable in both roots. Defaults are the short local
# aliases of ~/.aws/config (README, "AWS profile aliases").
PROD_AWS_PROFILE ?= prod
STAG_AWS_PROFILE ?= stag
LZA_AWS_PROFILE ?= lza-prod
LZA_STAG_AWS_PROFILE ?= lza-stag

# Refuse to create prod's roles in the stag account, or the reverse. Profile name
# is the guard because make cannot cheaply read the account ID — the `account_id`
# output exists so the result can be checked after the fact. EXPECTED_AWS_PROFILE
# is set in the token table above, per mode, so this file has one place that knows
# which account a PROD value means and the guard cannot drift from the env file.
ifneq ($(AWS_PROFILE),$(EXPECTED_AWS_PROFILE))
$(error For the $(TF_ENVIRONMENT) CI roles (PROD=$(PROD)), AWS_PROFILE must be '$(EXPECTED_AWS_PROFILE)' — override via the matching *_AWS_PROFILE variable)
endif

ifeq ($(strip $(FLIP_TFSTATE_BUCKET_NAME)),)
$(error Set FLIP_TFSTATE_BUCKET_NAME in $(MAIN_ENV_FILE))
endif

# The UI deploy role's policy has to name the bucket it may write (#1186), and this
# root is applied before the root that creates that bucket — so the name comes from
# the env file rather than from a `terraform output` on this side.
ifeq ($(strip $(FLIP_UI_BUCKET_NAME)),)
$(error Set FLIP_UI_BUCKET_NAME in $(MAIN_ENV_FILE) — the UI deploy role scopes its S3 write to that bucket)
endif

export TF_VAR_AWS_REGION = $(AWS_REGION)
export TF_VAR_environment = $(TF_ENVIRONMENT)
export TF_VAR_apply_branch = $(APPLY_BRANCH)
export TF_VAR_drift_branch = $(DRIFT_BRANCH)
export TF_VAR_github_environment = $(GITHUB_ENVIRONMENT)
export TF_VAR_state_bucket_name = $(FLIP_TFSTATE_BUCKET_NAME)
export TF_VAR_ui_bucket_name = $(FLIP_UI_BUCKET_NAME)

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
