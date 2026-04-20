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

# Dev-account Terraform root: Cognito + SES only.
#
# Shares the same env file as the local Docker Compose dev stack
# (../../../.env.development) so developers only maintain one set of
# credentials. The variables we need — AWS_PROFILE, AWS_REGION,
# SES_VERIFIED_EMAIL, ADMIN_USER_PASSWORD — already live there.
# Cognito admin/researcher emails are usually set in .env.development as
# well for seeding the local stack; reuse them here.

ENV_FILE := ../../../../.env.development

ifneq ("$(wildcard $(ENV_FILE))","")
include $(ENV_FILE)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(ENV_FILE))
endif

ifndef AWS_PROFILE
$(error AWS_PROFILE is not set. Add it to $(ENV_FILE))
endif

# Logical AWS profile names. Defaults match the short-alias pattern recommended
# in deploy/providers/AWS/README.md (operators add `[profile dev]` / `[profile stag]`
# / `[profile prod]` blocks to ~/.aws/config). Override via the env file or on the
# command line if your local profile names differ.
DEV_AWS_PROFILE  ?= dev
STAG_AWS_PROFILE ?= stag
PROD_AWS_PROFILE ?= prod

# Safety guards: refuse to touch the prod or stag account from this dev stack,
# and require the dev profile so a typo cannot land the dev stack against an
# unintended profile.
ifeq ($(AWS_PROFILE),$(PROD_AWS_PROFILE))
$(error AWS_PROFILE is the PROD account — refuse to run dev-stack commands against prod)
endif
ifeq ($(AWS_PROFILE),$(STAG_AWS_PROFILE))
$(error AWS_PROFILE is the STAG account — refuse to run dev-stack commands against stag)
endif
ifneq ($(AWS_PROFILE),$(DEV_AWS_PROFILE))
$(error AWS_PROFILE='$(AWS_PROFILE)' is not the expected dev profile '$(DEV_AWS_PROFILE)' (override via DEV_AWS_PROFILE))
endif

# Backend bucket for dev state. The bucket name comes from the shared
# FLIP_TFSTATE_BUCKET_NAME env var, but dev writes to its own backend key
# so it cannot collide with prod/stag state.
ifeq ($(strip $(FLIP_TFSTATE_BUCKET_NAME)),)
$(error Set FLIP_TFSTATE_BUCKET_NAME in $(ENV_FILE))
endif

# Terraform variables picked up via the TF_VAR_ env var convention.
# Seed the admin email from SES_VERIFIED_EMAIL by default — in dev that's
# usually the developer's own mailbox, which is what you want for
# receiving the Cognito invite. Override by setting
# flip_cognito_admin_email in .env.development.
TF_VAR_flip_cognito_admin_email ?= $(SES_VERIFIED_EMAIL)

export TF_VAR_AWS_REGION ?= $(AWS_REGION)
export TF_VAR_flip_user_pool_name ?= flip-user-pool-dev
export TF_VAR_flip_cognito_client ?= flip-cognito-client-dev
export TF_VAR_sign_in_hostname ?= localhost
export TF_VAR_flip_cognito_admin_email
export TF_VAR_flip_cognito_researcher_email ?= $(flip_cognito_researcher_email)
export TF_VAR_ADMIN_USER_PASSWORD := $(ADMIN_USER_PASSWORD)
export TF_VAR_SES_VERIFIED_EMAIL := $(SES_VERIFIED_EMAIL)

.PHONY: create-backend init plan apply destroy validate status fmt

create-backend:
	@echo "🚀 Creating Terraform backend resources..."
	@TFSTATE_BUCKET_NAME="$(FLIP_TFSTATE_BUCKET_NAME)" TFSTATE_KEY="flip/dev/terraform.tfstate" TFSTATE_REGION="$(AWS_REGION)" ../scripts/create-backend.sh

init:
	terraform init \
		-backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" \
		-backend-config="region=$(AWS_REGION)" \
		-reconfigure

validate:
	terraform validate

fmt:
	terraform fmt -recursive .
	terraform fmt -recursive ../modules/cognito
	terraform fmt -recursive ../modules/ses

plan:
	terraform plan

apply:
	terraform apply

destroy:
	terraform destroy

status:
	terraform state list
