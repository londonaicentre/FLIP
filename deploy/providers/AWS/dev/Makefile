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

# Dev-account Terraform root: Cognito + the three FLIP application S3 buckets
# (FLIP#24 — see ../README.md "FLIP application S3 buckets"). No SES: dev
# email is flip-api's console backend (FLIP#919).
#
# Shares the same env file as the local Docker Compose dev stack
# (../../../.env.development) so developers only maintain one set of
# credentials. AWS_PROFILE, AWS_REGION, ADMIN_USER_PASSWORD,
# FLIP_TFSTATE_BUCKET_NAME and the three bucket names already live there;
# flip_cognito_admin_email ships commented out in the example and must be
# added — the guard below errors at parse time until it is.

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

# Which goals actually need credentials-shaped configuration. A file-scope
# $(error) aborts the whole Makefile on *every* invocation, so an unguarded one
# takes `fmt`, `validate` and `status` down with it — targets that read no
# variable and reach no AWS account. Since FLIP_TFSTATE_BUCKET_NAME and
# flip_cognito_admin_email both ship commented out in .env.development.example
# (and the bucket names ship as `<placeholder>`), that turned a fresh checkout's
# first `make fmt` into a hard failure. The guards below are kept — seeding an
# empty admin address or a placeholder bucket silently would be worse — but
# scoped to the goals that consume the value (FLIP#1081 review).
#
# MAKECMDGOALS is empty for a bare `make`, which runs the first target below
# (create-backend), so treat that case as create-backend rather than as "no
# goals" and skip nothing it needs.
DEV_TF_GOALS := $(if $(MAKECMDGOALS),$(MAKECMDGOALS),create-backend)
# Terraform needs the state backend to init, plan or change anything.
DEV_TF_BACKEND_GOALS := $(filter create-backend init plan apply destroy,$(DEV_TF_GOALS))
# The TF_VAR_* values are only read when Terraform evaluates the configuration.
DEV_TF_APPLY_GOALS := $(filter plan apply destroy,$(DEV_TF_GOALS))

# Backend bucket for dev state. The bucket name comes from the shared
# FLIP_TFSTATE_BUCKET_NAME env var, but dev writes to its own backend key
# so it cannot collide with prod/stag state.
ifneq ($(DEV_TF_BACKEND_GOALS),)
ifeq ($(strip $(FLIP_TFSTATE_BUCKET_NAME)),)
$(error Set FLIP_TFSTATE_BUCKET_NAME in $(ENV_FILE))
endif
endif

# Terraform variables picked up via the TF_VAR_ env var convention.
# The Cognito seed-admin email is required and guarded below (next to the
# bucket guards, after unset_or_placeholder is defined).
export TF_VAR_AWS_REGION ?= $(AWS_REGION)
export TF_VAR_flip_user_pool_name ?= flip-user-pool-dev
export TF_VAR_flip_cognito_client ?= flip-cognito-client-dev
export TF_VAR_sign_in_hostname ?= localhost
export TF_VAR_flip_cognito_admin_email
export TF_VAR_flip_cognito_researcher_email ?= $(flip_cognito_researcher_email)
export TF_VAR_ADMIN_USER_PASSWORD := $(ADMIN_USER_PASSWORD)

# A copied-but-unedited .env leaves `<your-...>` placeholders in place. Those are non-empty, so an
# emptiness check passes them straight through to Terraform, which fails much later with an opaque
# AWS bucket-name error. Treat a placeholder as unset — same idea as XNAT_CREDENTIAL_PLACEHOLDER in
# trust/xnat/Makefile.
unset_or_placeholder = $(if $(1),$(if $(filter <%>,$(patsubst <%,<%>,$(1))),placeholder,),unset)

# Cognito seed-admin mailbox — receives the dev pool's invite email. Required,
# no default: set flip_cognito_admin_email in $(ENV_FILE) (usually the
# developer's own mailbox). It previously defaulted from SES_VERIFIED_EMAIL,
# which FLIP#919 removed along with the dev SES module.
ifneq ($(DEV_TF_APPLY_GOALS),)
$(if $(call unset_or_placeholder,$(flip_cognito_admin_email)),$(error 📧 flip_cognito_admin_email must be set to a real mailbox in $(ENV_FILE) — it seeds the Cognito admin user (previously defaulted from the removed SES_VERIFIED_EMAIL)),)
endif
TF_VAR_flip_cognito_admin_email := $(flip_cognito_admin_email)

# FLIP application S3 buckets (FLIP#24). Required, no defaults — value lives
# in $(ENV_FILE). Fail fast with a useful message if missing.
ifneq ($(DEV_TF_APPLY_GOALS),)
$(if $(call unset_or_placeholder,$(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)),$(error 🪣 FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME must be set to a real bucket name in $(ENV_FILE)),)
$(if $(call unset_or_placeholder,$(FLIP_FL_RESULTS_BUCKET_NAME)),$(error 🪣 FLIP_FL_RESULTS_BUCKET_NAME must be set to a real bucket name in $(ENV_FILE)),)
$(if $(call unset_or_placeholder,$(FLIP_APP_BUNDLES_BUCKET_NAME)),$(error 🪣 FLIP_APP_BUNDLES_BUCKET_NAME must be set to a real bucket name in $(ENV_FILE)),)
endif
export TF_VAR_FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME := $(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)
export TF_VAR_FLIP_FL_RESULTS_BUCKET_NAME          := $(FLIP_FL_RESULTS_BUCKET_NAME)
export TF_VAR_FLIP_APP_BUNDLES_BUCKET_NAME         := $(FLIP_APP_BUNDLES_BUCKET_NAME)

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

plan:
	terraform plan

apply:
	terraform apply

destroy:
	terraform destroy

status:
	terraform state list
