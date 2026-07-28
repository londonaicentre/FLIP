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

# e2e-lza Terraform root: LZA ingress end-to-end test stack (see README.md).
#
# Unlike dev/ (which shares .env.development with the compose stack), this
# stack has its own env file — the sealed workload account is new and shares
# no credentials or buckets with any existing FLIP environment.

ENV_FILE := .env.e2e

ifneq ("$(wildcard $(ENV_FILE))","")
include $(ENV_FILE)
export $(shell sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(ENV_FILE))
else
$(error $(ENV_FILE) not found — cp .env.e2e.example .env.e2e and fill in the workload-account values)
endif

ifndef AWS_PROFILE
$(error AWS_PROFILE is not set. Add it to $(ENV_FILE))
endif

# Logical AWS profile names for the EXISTING FLIP accounts (same defaults as
# dev/Makefile). This stack must only ever run against the new sealed LZA
# workload account, so all three are refused outright.
DEV_AWS_PROFILE  ?= dev
STAG_AWS_PROFILE ?= stag
PROD_AWS_PROFILE ?= prod

ifeq ($(AWS_PROFILE),$(PROD_AWS_PROFILE))
$(error AWS_PROFILE is the PROD account — the e2e-lza stack targets the sealed LZA workload account only)
endif
ifeq ($(AWS_PROFILE),$(STAG_AWS_PROFILE))
$(error AWS_PROFILE is the STAG account — the e2e-lza stack targets the sealed LZA workload account only)
endif
ifeq ($(AWS_PROFILE),$(DEV_AWS_PROFILE))
$(error AWS_PROFILE is the DEV account — the e2e-lza stack targets the sealed LZA workload account only)
endif

ifeq ($(strip $(FLIP_TFSTATE_BUCKET_NAME)),)
$(error Set FLIP_TFSTATE_BUCKET_NAME in $(ENV_FILE))
endif

ifeq ($(strip $(TF_VAR_vpc_cidr)),)
$(error Set TF_VAR_vpc_cidr in $(ENV_FILE) — the CIDR needs LZA IPAM sign-off, see README open question 1)
endif

export TF_VAR_AWS_REGION ?= $(AWS_REGION)

.PHONY: create-backend init validate fmt plan apply destroy status

create-backend:
	@echo "🚀 Creating Terraform backend resources..."
	@TFSTATE_BUCKET_NAME="$(FLIP_TFSTATE_BUCKET_NAME)" TFSTATE_KEY="flip/e2e-lza/terraform.tfstate" TFSTATE_REGION="$(AWS_REGION)" ../scripts/create-backend.sh

init:
	terraform init \
		-backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" \
		-backend-config="region=$(AWS_REGION)" \
		-reconfigure

validate:
	terraform validate

fmt:
	terraform fmt -recursive .
	terraform fmt -recursive ../modules/secgroup

plan:
	terraform plan

apply:
	terraform apply

destroy:
	terraform destroy

status:
	terraform state list
