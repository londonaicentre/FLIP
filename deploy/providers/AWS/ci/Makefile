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

# Terraform-CI bootstrap for an account you own (see README.md). Applied from a
# laptop, never by CI: the roles CI assumes must not be managed by CI, or a bad
# apply locks the pipeline out of the apply that would fix it.
#
#     make init && make plan && make apply     # first run, local state
#     make migrate-state                       # then move the state into the bucket
#
# One tfvars file per account; TFVARS selects it. Credentials are whatever the
# AWS CLI resolves (AWS_PROFILE, SSO), and the provider's allowed_account_ids is
# the guard against the wrong account.

TFVARS ?= terraform.tfvars

# Read from the tfvars file, so the backend and the preflights use the same values
# Terraform does. Each can be overridden on the command line.
_tfvar = $(shell sed -n 's/^[[:space:]]*$(1)[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' $(TFVARS) 2>/dev/null | head -1)
STATE_BUCKET ?= $(call _tfvar,state_bucket_name)
STATE_REGION ?= $(call _tfvar,aws_region)
APPLY_ROLE_NAME ?= $(or $(call _tfvar,apply_role_name),AICentre-FLIPTerraformApplyRole)

# Static AWS_* env vars take priority over the SSO chain in both the CLI and the
# Go SDK, and a stale or empty one produces InvalidClientTokenId errors.
_UNSET_STATIC := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
_TF := $(_UNSET_STATIC); terraform
_ACCOUNT_LABEL := $(if $(AWS_PROFILE),the $(AWS_PROFILE) account,the current credentials' account)

.PHONY: check-tfvars
check-tfvars:
	@if [ ! -f "$(TFVARS)" ]; then \
		echo "❌ $(TFVARS) not found — copy terraform.tfvars.example and fill it in."; \
		exit 1; \
	fi

.PHONY: init
init: check-tfvars ## Initialise (local state until migrate-state has run)
	@if [ -f backend.tf ]; then \
		$(_TF) init -backend-config="bucket=$(STATE_BUCKET)" -backend-config="region=$(STATE_REGION)"; \
	else \
		$(_TF) init; \
	fi

# The roles trust GitHub's OIDC identity provider, which this root looks up rather
# than declares: an account holds one provider per issuer URL, shared by anything
# GitHub-driven in it, so it belongs to whatever owns the account's baseline IAM —
# and declared here, a destroy of FLIP's CI would delete it from under everything
# else. Terraform's own error when it is missing ("no matching OpenID Connect
# Provider found") says nothing about why or who, so check first and say both.
.PHONY: check-oidc-provider
check-oidc-provider: ## Fail early, and say what to do, if the account has no GitHub OIDC provider
	@$(_UNSET_STATIC); \
	if ! arns=$$(aws iam list-open-id-connect-providers \
		--query 'OpenIDConnectProviderList[].Arn' --output text 2>&1); then \
		echo "❌ Could not list the OIDC providers in $(_ACCOUNT_LABEL), so cannot tell"; \
		echo "   whether one exists — an expired SSO session is the usual cause:"; \
		echo "   $$arns" | tail -1; \
		exit 1; \
	fi; \
	if ! printf '%s\n' "$$arns" | tr '\t' '\n' | grep -q 'oidc-provider/token.actions.githubusercontent.com$$'; then \
		echo "❌ No GitHub Actions OIDC identity provider in $(_ACCOUNT_LABEL)."; \
		echo "   The CI roles trust it, and this root deliberately does not create it: it is"; \
		echo "   account-level plumbing shared by anything GitHub-driven in the account, so it"; \
		echo "   belongs with the account's baseline IAM, where FLIP's CI cannot delete it."; \
		echo "   - AI Centre accounts are owned by aicentre-iac / aicentre-lza-iac, which also"; \
		echo "     bootstrap FLIP's CI there — this root must not be run in them."; \
		echo "   - Any other account: declare it in your own baseline IaC, e.g."; \
		echo "       resource \"aws_iam_openid_connect_provider\" \"github\" {"; \
		echo "         url            = \"https://token.actions.githubusercontent.com\""; \
		echo "         client_id_list = [\"sts.amazonaws.com\"]"; \
		echo "       }"; \
		echo "     or, once: aws iam create-open-id-connect-provider \\"; \
		echo "       --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com"; \
		exit 1; \
	fi

# An empty state here and an apply role already in the account means something
# else owns the bootstrap — a platform repository, or an earlier copy of this root
# whose state is elsewhere. Creating it again would fail on EntityAlreadyExists at
# best, and at worst give the same roles two owners that undo each other.
.PHONY: check-not-managed-elsewhere
check-not-managed-elsewhere: ## Refuse to create roles that already exist in the account
	@$(_UNSET_STATIC); \
	if ! resources=$$(terraform state list 2>&1); then \
		case "$$resources" in \
			*"No state file was found"*) resources="" ;; \
			*) echo "❌ Could not read this root's state — run 'make init' first:"; \
			   echo "   $$resources" | head -3; \
			   exit 1 ;; \
		esac; \
	fi; \
	if [ -n "$$resources" ]; then exit 0; fi; \
	if out=$$(aws iam get-role --role-name "$(APPLY_ROLE_NAME)" --query Role.RoleName --output text 2>&1); then \
		echo "❌ $(APPLY_ROLE_NAME) already exists in $(_ACCOUNT_LABEL), but this root's state is empty."; \
		echo "   The bootstrap is managed elsewhere — at AI Centre by aicentre-iac / aicentre-lza-iac."; \
		echo "   If it is genuinely yours, import the existing objects (README.md, \"Roles that already"; \
		echo "   exist\") or point this root at the state that already holds them."; \
		exit 1; \
	fi; \
	case "$$out" in \
		*NoSuchEntity*) ;; \
		*) echo "❌ Could not check whether $(APPLY_ROLE_NAME) exists in $(_ACCOUNT_LABEL):"; \
		   echo "   $$out" | tail -1; \
		   exit 1 ;; \
	esac

.PHONY: plan
plan: check-tfvars check-oidc-provider check-not-managed-elsewhere ## Plan the bootstrap
	@$(_TF) plan -var-file="$(TFVARS)" -out=plan.tfplan

.PHONY: apply
apply: ## Apply the saved plan
	@$(_TF) apply plan.tfplan
	@echo ""
	@echo "Wire plan_role_arn / apply_role_arn into the GitHub environment as"
	@echo "TF_PLAN_ROLE_ARN / TF_APPLY_ROLE_ARN:"
	@$(_TF) output
	@if [ ! -f backend.tf ]; then \
		echo ""; \
		echo "State is still local (terraform.tfstate). Run 'make migrate-state' to move it"; \
		echo "into $(STATE_BUCKET)."; \
	fi

.PHONY: output
output: ## Print the role ARNs and the OIDC claims they expect
	@$(_TF) output

.PHONY: migrate-state
migrate-state: check-tfvars ## Move local state into the state bucket the first apply created
	@if [ -f backend.tf ]; then \
		echo "❌ backend.tf already exists — the state has been migrated. 'make init' reattaches it."; \
		exit 1; \
	fi
	@if [ -z "$(STATE_BUCKET)" ] || [ -z "$(STATE_REGION)" ]; then \
		echo "❌ state_bucket_name / aws_region not found in $(TFVARS) (or set STATE_BUCKET= STATE_REGION=)."; \
		exit 1; \
	fi
	cp backend.s3.tf.example backend.tf
	$(_TF) init -migrate-state \
		-backend-config="bucket=$(STATE_BUCKET)" \
		-backend-config="region=$(STATE_REGION)"
	$(_TF) state list
	@echo ""
	@echo "✅ State is now in s3://$(STATE_BUCKET)/flip/ci/terraform.tfstate."
	@echo "   Check the list above matches what was applied, then delete the local copies:"
	@echo "       rm terraform.tfstate terraform.tfstate.backup"

.PHONY: help
help: ## Show this help message
	@echo "Terraform-CI bootstrap (TFVARS=$(TFVARS)):"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
