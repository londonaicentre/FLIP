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

# What HUB means (ENV, the env-file name, ENV_CLASS, …) is derived once in
# ../../env_mode.mk, shared with every Makefile that reads HUB. Unlike the root
# Makefile, an unset HUB is no estate here: every goal but the static ones below
# refuses to run without HUB=stag|prod (FLIP#1377). Every estate is an LZA one; see
# "Deploying onto an LZA estate" in README.md.
READS_HUB_ENV_FILE := yes
include ../../env_mode.mk

# Goals that need no env file and call no AWS API. When every goal on the command line is one of
# these, the parse-time env guards below stand aside, so a contributor without the gitignored
# deploy env files can run them. Any other goal, alone or beside these, keeps every guard (FLIP#1440).
# `override`, and trusting MAKECMDGOALS only when make set it, keep the switch out of reach of the
# command line, MAKEFLAGS and the environment: one variable must not turn off the account guard.
override STATIC_GOALS := lint fmt-check tflint-lint checkov-lint help
_GOALS_FROM_OUTSIDE := $(or $(findstring environment,$(origin MAKECMDGOALS)),$(findstring command,$(origin MAKECMDGOALS)),$(findstring override,$(origin MAKECMDGOALS)))
override STATIC_ONLY := $(if $(_GOALS_FROM_OUTSIDE),,$(if $(MAKECMDGOALS),$(if $(filter-out $(STATIC_GOALS),$(MAKECMDGOALS)),,yes)))
MAIN_ENV_FILE=../../../$(ENV_FILE_NAME)
ifeq ($(STATIC_ONLY),)
$(if $(IS_DEPLOYED),,$(error HUB=stag or HUB=prod is required: these targets act on a deployed hub, and an unset HUB means development))
endif

# The trust node's two deployment shapes live beside the trust compose files, not in
# this provider (#1213). Spelled once; every recipe that reaches them goes through these.
REPO_ROOT := $(abspath $(dir $(firstword $(MAKEFILE_LIST)))../../..)
HELM_CHART_DIR := $(REPO_ROOT)/trust/deploy/helm
ONPREM_ANSIBLE_DIR := $(REPO_ROOT)/trust/deploy/ansible

ifneq ($(IS_DEPLOYED),)
ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
# Real assignments only, as in the root Makefile: a commented `# KEY=` must not become a bare export.
export $(shell sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' $(MAIN_ENV_FILE))
endif
endif

# Derive backend-specific DOCKER_FL_{API,SERVER,CLIENT}_NAME and FL_PROVISIONED_DIR
# from FL_BACKEND. The root Makefile and trust/Makefile already include this
# after their .env* block; running `make plan/apply` directly from this directory
# would otherwise leave the DOCKER_FL_* names empty and produce invalid ECS image
# references like `ghcr.io/londonaicentre/:stag`.
include ../../fl_backend.mk

LOCK ?= true

# Export HUB so child processes (e.g. check_status.py) can determine the environment
export HUB

# Terraform and AWS CLI prefixes: unset static credential env vars that
# can leak from the parent shell (.env.<env> may carry empty/literal-quoted
# AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY for the omop-db init job).
# These env vars take priority over the AWS_PROFILE / SSO credential chain
# in the AWS Go SDK (Terraform) and cause InvalidClientTokenId errors.
_TF := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; terraform
_AWS_ENV := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; AWS_PROFILE=$(AWS_PROFILE)

# Resolve FL_KIT_DATE from the backend-specific kit date: FLARE_KIT_DATE or FLOWER_KIT_DATE
FL_KIT_DATE := $(if $(filter nvflare,$(FL_BACKEND)),$(FLARE_KIT_DATE),$(FLOWER_KIT_DATE))

# Logical AWS profile names: the short local aliases recommended in README.md for each LZA
# workload account's Identity Center `FLIPAdminAccess` permission set. Override via the env
# file or on the command line if your local profile names differ.
PROD_AWS_PROFILE ?= lza-prod
STAG_AWS_PROFILE ?= lza-stag

# The AWS profile each environment expects, checked by the account guard in the env guards
# below. Profile name is the safety guard because Make has no easy way to read the account ID
# without an `aws` call. One knob per environment, looked up by ENV.
_AWS_PROFILE_KNOB_prod := PROD_AWS_PROFILE
_AWS_PROFILE_KNOB_stag := STAG_AWS_PROFILE
EXPECTED_AWS_PROFILE := $($(_AWS_PROFILE_KNOB_$(ENV)))

## SETTING VARIABLES FOR TERRAFORM
# The environment class (prod|stag, from env_mode.mk). Used in main.tf to gate
# prod-only RDS hardening (deletion protection, final snapshot).
export TF_VAR_environment=$(ENV_CLASS)

# Name tag of the platform-managed VPC. Set LZA_VPC_NAME in the env file to override the variables.tf default (AWSAccelerator-eu-west-2-prod) —
# optional on HUB=prod if the platform template keeps that name, mandatory on
# HUB=stag (the env guards below).
ifneq ($(LZA_VPC_NAME),)
export TF_VAR_lza_vpc_name=$(LZA_VPC_NAME)
endif

# IAM permissions boundary (FLIP#1082, FLIP#1280): the platform's AICentre-WorkloadRoleBoundary
# (londonaicentre/lza#51), which the accelerator deploys to every workload account. An LZA SCP
# denies creating a role, or attaching or writing a policy onto one, unless the role carries it,
# so every role change needs it, from a laptop or from CI. An env file that sets the variable
# still wins: `?=` only supplies the default.
export TF_VAR_iam_permissions_boundary_name ?= AICentre-WorkloadRoleBoundary

# The trust kit file the trust-deploy targets read — trust/.env.<KIT>.$(ENV), or an old
# name resolved by env_mode.mk's kit_file.
ifeq ($(origin KIT_FILE),undefined)
KIT_FILE := $(call kit_file,$(REPO_ROOT)/trust/,$(KIT))
endif
KIT_FILE_LABEL := $(patsubst $(REPO_ROOT)/%,%,$(KIT_FILE))
$(if $(KIT),$(call kit_hint,$(KIT_FILE_LABEL)))
# Only the trust security group's DNS rules read it (module.trust_security_group).
export TF_VAR_vpc_cidr=10.0.0.0/16
export TF_VAR_flip_keypair=~/.ssh/host-aws
export TF_VAR_ec2_public_key_path=$(HOME)/.ssh/host-aws.pub
export TF_VAR_flip_user_pool_name=flip-user-pool
export TF_VAR_flip_cognito_client=flip-cognito-client
export TF_VAR_FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME=${FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME}
export TF_VAR_FLIP_FL_RESULTS_BUCKET_NAME=${FLIP_FL_RESULTS_BUCKET_NAME}
export TF_VAR_FLIP_APP_BUNDLES_BUCKET_NAME=${FLIP_APP_BUNDLES_BUCKET_NAME}
export TF_VAR_AICENTRE_BUCKET_NAME=${AICENTRE_BUCKET_NAME}
# Optional access-log bucket name override (empty = derived from flip_alb_subdomain; see variables.tf)
export TF_VAR_ACCESS_LOGS_BUCKET_NAME=${ACCESS_LOGS_BUCKET_NAME}
# A copied-but-unedited .env leaves `<your-...>` placeholders in place. Those are non-empty, so an
# emptiness check passes them straight through to Terraform, which fails much later with an opaque
# AWS bucket-name error. Treat a placeholder as unset — same idea as XNAT_CREDENTIAL_PLACEHOLDER in
# trust/xnat/Makefile.
unset_or_placeholder = $(if $(1),$(if $(filter <%>,$(patsubst <%,<%>,$(1))),placeholder,),unset)

export TF_VAR_flip_cognito_admin_email=aicentreflip@gmail.com
export TF_VAR_flip_cognito_researcher_email=rafaelagd@gmail.com
export TF_VAR_POSTGRES_DB=${POSTGRES_DB}
export TF_VAR_AES_KEY_BASE64=${AES_KEY_BASE64}
export TF_VAR_INTERNAL_SERVICE_KEY_HASH=${INTERNAL_SERVICE_KEY_HASH}
export TF_VAR_INTERNAL_SERVICE_KEY=${INTERNAL_SERVICE_KEY}
export TF_VAR_INTERNAL_SERVICE_KEY_HEADER=${INTERNAL_SERVICE_KEY_HEADER}
# ── Fail-fast env guards ────────────────────────────────────────────────────────────────────
# Parse-time refusals of a deploy env file that cannot work. They stand aside only when every
# goal is static (STATIC_ONLY, at the top); any other goal keeps all of them.
ifeq ($(STATIC_ONLY),)
$(if $(FL_KIT_DATE),,$(error 🧠 $(if $(filter nvflare,$(FL_BACKEND)),FLARE_KIT_DATE,FLOWER_KIT_DATE) must be set in the environment file for FL_BACKEND=$(FL_BACKEND)))
# Refuse to operate against an unintended AWS account (the profile knobs above).
ifneq ($(AWS_PROFILE),$(EXPECTED_AWS_PROFILE))
$(error "AWS_PROFILE must be set to '$(EXPECTED_AWS_PROFILE)' for HUB=$(HUB) (or set $(_AWS_PROFILE_KNOB_$(ENV)) in $(MAIN_ENV_FILE)) — got '$(AWS_PROFILE)'")
endif
# The variables.tf default VPC name is the prod template (AWSAccelerator-eu-west-2-prod);
# there is no defaultable name for the staging VPC, so require it explicitly rather
# than letting the data lookup fail with an opaque "no results" in the wrong account.
ifeq ($(ENV),stag)
ifeq ($(LZA_VPC_NAME),)
$(error "For staging deployments, LZA_VPC_NAME must be set in $(MAIN_ENV_FILE) (the Name tag of the staging platform VPC)")
endif
endif
# No default either way: true on an account without a Route 53 zone fails the zone lookup, and
# false on one with it plans the certificate and its records away.
$(if $(filter true false,$(MANAGE_DNS)),,$(error MANAGE_DNS must be true or false in $(MAIN_ENV_FILE) — false while the account has no Route 53 hosted zone (FLIP#749); got '$(MANAGE_DNS)'))
# Fail fast with a useful message if the per-tenant bucket names are missing
# from the env file. The variables are required (no default) so Terraform
# would otherwise prompt mid-plan with a generic message that gives no hint
# about where to set the value.
$(if $(call unset_or_placeholder,$(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)),$(error 🪶 FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME must be set to a real bucket name in $(MAIN_ENV_FILE)),)
$(if $(call unset_or_placeholder,$(FLIP_FL_RESULTS_BUCKET_NAME)),$(error 🪶 FLIP_FL_RESULTS_BUCKET_NAME must be set to a real bucket name in $(MAIN_ENV_FILE)),)
$(if $(call unset_or_placeholder,$(FLIP_APP_BUNDLES_BUCKET_NAME)),$(error 🪶 FLIP_APP_BUNDLES_BUCKET_NAME must be set to a real bucket name in $(MAIN_ENV_FILE)),)
# AICENTRE_BUCKET_NAME is guarded on the same terms: `variable "AICENTRE_BUCKET_NAME"` has no
# default, and it also names the bucket the FL participant-kit targets below read and write.
$(if $(call unset_or_placeholder,$(AICENTRE_BUCKET_NAME)),$(error 🪶 AICENTRE_BUCKET_NAME must be set to a real bucket name in $(MAIN_ENV_FILE)),)
# Fail fast with a useful message when these are missing — the variable is
# `sensitive` with no default, so otherwise Terraform errors out mid-plan
# with a generic "no value for required variable" that gives no hint about
# where the value should come from.
$(if $(INTERNAL_SERVICE_KEY),,$(error 🔑 INTERNAL_SERVICE_KEY must be set in $(MAIN_ENV_FILE) — generate with `make -C ../../../flip-api generate-internal-service-key`))
$(if $(INTERNAL_SERVICE_KEY_HASH),,$(error 🔑 INTERNAL_SERVICE_KEY_HASH must be set in $(MAIN_ENV_FILE) — must be sha256 of INTERNAL_SERVICE_KEY))
$(if $(INTERNAL_SERVICE_KEY_HEADER),,$(error 📨 INTERNAL_SERVICE_KEY_HEADER must be set in $(MAIN_ENV_FILE)))
endif

# The GPUs fl-api on ECS writes into a job that declares none (mirrors compose).
# Default 0 in variables.tf, and keep it there: jobs declare their own in their
# meta.json (FLIP#70), and a non-zero default keeps every job off a CPU-only trust.
# Export only when set: an unconditional export turns a missing env key into
# TF_VAR_…="" and Terraform rejects the empty string for a number variable
# instead of falling back to the variables.tf default.
ifneq ($(JOB_RESOURCE_SPEC_NUM_GPUS),)
export TF_VAR_JOB_RESOURCE_SPEC_NUM_GPUS=${JOB_RESOURCE_SPEC_NUM_GPUS}
endif
# Uncomment to enable ECS Exec on Fargate tasks for debugging:
# export TF_VAR_ecs_exec_enabled=true

export TF_VAR_docker_image_tag=${DOCKER_TAG}
export TF_VAR_flip_fl_image_tag=${DOCKER_FL_TAG}
# The FL API's own tag (FLIP#1283). Only CI's resolve-image-tags.sh sets it — a
# release apply pins `v<X.Y.Z>@sha256:…`, and that digest is fl-api's, not
# fl-server's. Exported only when set so every other caller keeps variables.tf's
# empty default, which ecs_tasks.tf reads as "same tag as the FL server".
ifneq ($(DOCKER_FL_API_TAG),)
export TF_VAR_fl_api_image_tag=${DOCKER_FL_API_TAG}
endif
export TF_VAR_docker_registry=${DOCKER_REGISTRY}
# One-shot EFS-provision utility image (default amazon/aws-cli from Docker Hub
# in variables.tf). An LZA account has no internet egress, so its env file
# points this at the in-account ECR Public pull-through cache mirror
# (…/ecr-public/aws-cli/aws-cli:<tag>, FLIP#749). Export only when set, so an unset
# key keeps the variables.tf default rather than an empty string.
ifneq ($(EFS_PROVISION_IMAGE),)
export TF_VAR_efs_provision_image=${EFS_PROVISION_IMAGE}
endif
export TF_VAR_fl_api_name=${DOCKER_FL_API_NAME}
export TF_VAR_fl_server_name=${DOCKER_FL_SERVER_NAME}
export TF_VAR_fl_backend=${FL_BACKEND}
export TF_VAR_flare_kit_date=${FLARE_KIT_DATE}
export TF_VAR_flower_kit_date=${FLOWER_KIT_DATE}
export TF_VAR_POSTGRES_USER=${POSTGRES_USER}
export TF_VAR_FL_SERVER_PORT=${FL_SERVER_PORT}
# Whether Terraform provisions the cloud Trust EC2. false = hub-only deployment
# (every trust runs on-prem — e.g. the workloads need a GPU the t3 trust host
# doesn't have). Settable in the env file or CLI; `make full-deploy-hub-only`
# forces it for its whole chain.
DEPLOY_TRUST_EC2 ?= true
export TF_VAR_deploy_trust_ec2=$(DEPLOY_TRUST_EC2)
export TF_VAR_AWS_REGION=${AWS_REGION}
# DNS availability (FLIP#749): an LZA account has no Route53 hosted zone until
# the platform-side DNS migration lands, so its env file sets MANAGE_DNS=false
# (no custom certs/records) and flips it to true once the zone exists there.
# Required, no default (the env guards above).
export TF_VAR_manage_dns=$(MANAGE_DNS)
export TF_VAR_ADMIN_USER_PASSWORD=${ADMIN_USER_PASSWORD}
export TF_VAR_SES_VERIFIED_EMAIL=${SES_VERIFIED_EMAIL}
export TF_VAR_flip_alb_subdomain=${ALB_SUBDOMAIN}
export TF_VAR_flip_nlb_subdomain=${NLB_SUBDOMAIN}
export TF_VAR_FLIP_UI_BUCKET_NAME=${FLIP_UI_BUCKET_NAME}
# Optional: bucket holding the public Ark+ demo download bundles, served via
# CloudFront at /ark_demo/assets/*. Unset => no demo-assets resources (stag).
export TF_VAR_DEMO_ASSETS_BUCKET_NAME=${DEMO_ASSETS_BUCKET_NAME}
# flip-api / fl-* env vars consumed by the ECS task definitions (locals.tf) —
# the canonical definition of production container config (#936). The prod
# compose files mirror a subset as the local prod-image harness only; the hub
# no longer deploys on EC2+compose.
export TF_VAR_TRUST_API_KEY_HEADER=${TRUST_API_KEY_HEADER}
export TF_VAR_INTERNAL_SERVICE_KEY_HEADER=${INTERNAL_SERVICE_KEY_HEADER}
export TF_VAR_FL_ADMIN_DIRECTORY=${FL_ADMIN_DIRECTORY}
export TF_VAR_ENFORCE_MFA=${ENFORCE_MFA}
# JSON list of FL kit-slot names; seeds the fl_kit_slot pool register_trust
# claims from. Empty ⇒ NoFreeKitSlotError on registration. Set in the env file.
export TF_VAR_FL_KIT_SLOT_NAMES=${FL_KIT_SLOT_NAMES}
ECS_CLUSTER ?= flip-cluster
CENTRALHUB_ECS_SERVICES = flip-api fl-api-net-1 fl-server-net-1

# Immutable Central Hub deploys (FLIP#751): CI publishes a `sha-<short7>` tag for every
# image; deploy-centralhub resolves that tag from the tip of the env's branch and
# registers new task-definition revisions pointing at it, so what is live is always a
# recorded commit and rollback-centralhub can repoint at the previous revision. The
# env-file tags (DOCKER_TAG / DOCKER_FL_TAG) remain the bootstrap-only defaults Terraform
# bakes into a (re)created task-definition family — day-to-day deploys never touch them.
# Since FLIP#1204 a release also publishes `:v<X.Y.Z>` on every image (built at the release
# commit, no path filter), so `make deploy-centralhub HUB=prod TAG=v<X.Y.Z>` deploys the
# same tag the trust sites pin — one release identity across hub and sites.
# HUB=prod tracks origin/main, HUB=stag origin/develop.
DEPLOY_GIT_REF := $(if $(filter prod,$(ENV_CLASS)),origin/main,origin/develop)

TRUST_IMAGES = \
	$(DOCKER_REGISTRY)trust-api:$(DOCKER_TAG) \
	$(DOCKER_REGISTRY)imaging-api:$(DOCKER_TAG) \
	$(DOCKER_REGISTRY)data-access-api:$(DOCKER_TAG)

.PHONY: trigger-builds
trigger-builds: ## Trigger all GitHub Actions "Build and Push" workflows for the current branch
	@echo "🔨 Triggering GitHub Actions build workflows..."
	@branch=$$(git rev-parse --abbrev-ref HEAD) && \
	gh workflow list --json name,id \
	  --jq '.[] | select(.name | startswith("Build and Push")) | .id' | \
	while read -r id; do \
	  gh workflow run "$$id" --ref "$$branch"; \
	done
	@echo "✅ Build workflows triggered!"

.PHONY: check-ssm-ready
check-ssm-ready: ## Verify SSM Session Manager prerequisites are installed
	@echo "🔍 Checking SSM Session Manager prerequisites..."
	@command -v aws >/dev/null 2>&1 || { echo "❌ AWS CLI not found. Install: brew install awscli or apt-get install awscli"; exit 1; }
	@session-manager-plugin --version >/dev/null 2>&1 || { echo "❌ Session Manager plugin not found. Install: brew install session-manager-plugin (macOS) or see README.md"; exit 1; }
	@ssh-add -l >/dev/null 2>&1 || echo "⚠️  SSH agent has no keys (this is OK, SSM uses AWS credentials instead)"
	@[ -f ~/.ssh/host-aws ] || { echo "⚠️  SSH key ~/.ssh/host-aws not found (will be needed for Ansible)"; }
	@echo "✅ SSM prerequisites verified!"

prettier: ## Format Terraform files. YAML files are formatted by the prettier pre-commit hook.
	terraform fmt -recursive

.PHONY: lint fmt-check tflint-lint
lint: fmt-check tflint-lint checkov-lint ## terraform fmt, TFLint and Checkov, as validate_terraform.yml runs them — no env file or credentials needed (FLIP#1440)

fmt-check: ## terraform fmt -check over this tree (CI's fmt step)
	terraform fmt -check -recursive

tflint-lint: ## Static tflint lint (terraform recommended preset + AWS ruleset, .tflint.hcl) — no credentials needed
	bash scripts/tflint_lint.sh

.PHONY: checkov-lint
checkov-lint: ## Static checkov security lint (IAM policy content + promoted posture checks) over this Terraform tree — no credentials or init needed (FLIP#1052, FLIP#1058)
	bash scripts/checkov_lint.sh

.PHONY: aws-login
aws-login: ## Login to AWS SSO (required before terraform operations)
	@echo "🔐 Logging into AWS SSO..."
	@aws sso login --profile $(AWS_PROFILE) --use-device-code
	@echo "✅ AWS SSO login successful!"

.PHONY: github-login
github-login: ## Login to GitHub CLI (required before terraform operations)
	@echo "🔐 Checking GitHub CLI login status..."
	if gh auth status >/dev/null 2>&1; then \
		echo "✅ Already logged into GitHub CLI!"; \
	else \
		gh auth login; \
	fi

.PHONY: init
init: ## Initialize Terraform configuration
	@echo "🔧 Initializing Terraform..."
	@echo "Using AWS profile: $(AWS_PROFILE)"
	@echo "Using backend bucket: $(FLIP_TFSTATE_BUCKET_NAME)"
	@echo "Using AWS region: $(AWS_REGION)"
	@$(_TF) init \
		-backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" \
		-backend-config="region=$(AWS_REGION)" \
		-reconfigure


# Hand the resolved TF_VAR_* environment to a caller that runs terraform directly.
#
# The Terraform-input mapping lives in this Makefile and nowhere else: it derives
# TF_VAR_environment from HUB, hard-codes the VPC/subnet CIDRs, resolves the FL
# image names through deploy/fl_backend.mk and picks the kit date for FL_BACKEND.
# CI runs `op5dev/tf-via-pr`, which invokes terraform directly rather than through
# make, so it needs those values in its own environment — this target hands them
# over instead of CI re-deriving the mapping and drifting from a laptop plan:
#
#     make print-tf-env >> "$$GITHUB_ENV"
#
# Every line of stdout is consumed as KEY=value, so the recipe must print the
# variables and nothing else — no @echo banner, and no `##` recipe-line comments
# (those are echoed by make and would land in the file as garbage). Output carries
# AES_KEY_BASE64, INTERNAL_SERVICE_KEY, ADMIN_USER_PASSWORD and the DB user, so
# never pipe it anywhere it would be echoed. Values are single-line by construction
# (scripts/compose-ci-env.sh rejects embedded newlines), which is what makes the
# plain KEY=value form safe for $$GITHUB_ENV.
.PHONY: print-tf-env
print-tf-env: ## Print the resolved TF_VAR_* environment as KEY=value lines (for CI)
	@env | grep '^TF_VAR_' | sort

.PHONY: force-unlock
force-unlock: ## Force unlock Terraform state - provide LOCK_ID=<id> as argument
	@if [ -z "$(LOCK_ID)" ]; then \
		echo "❌ LOCK_ID not provided. Usage: make force-unlock LOCK_ID=<lock-id>"; \
		exit 1; \
	fi
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; bash scripts/force-unlock.sh $(LOCK_ID)

.PHONY: plan
plan: ## Plan Terraform changes (with environment file loaded)
	@echo "📋 Planning Terraform changes..."
	@$(_TF) plan -out=plan.tfplan -lock=${LOCK}

.PHONY: plan-cert
plan-cert: ## Plan only the ACM certificate (step 1 of 2 for HTTPS)
	@echo "📋 Planning ACM certificate..."
	@$(_TF) plan -target=aws_acm_certificate.flip -out=plan-cert.tfplan

.PHONY: apply-cert
apply-cert: ## Apply only the ACM certificate (step 1 of 2 for HTTPS)
	@echo "🔒 Applying ACM certificate (Step 1/2)..."
	@$(_TF) apply -target=aws_acm_certificate.flip -auto-approve
	@echo "✅ Certificate created! Now run 'make plan' and 'make apply' to deploy DNS validation."

.PHONY: plan-fl-kit-slots
plan-fl-kit-slots: ## Plan only the /flip/fl_kit_slot_names SSM parameter (+ flip-api task-role read)
	@echo "📋 Planning FL kit-slot parameter..."
	@$(_TF) plan -target=aws_ssm_parameter.fl_kit_slot_names \
		-target=aws_iam_role_policy.ecs_flip_api_task -out=plan-fl-kit-slots.tfplan

.PHONY: apply-fl-kit-slots
apply-fl-kit-slots: ## Apply only the FL kit-slot SSM parameter — the slot activation path
	## Re-renders /flip/fl_kit_slot_names from the env file's FL_KIT_SLOT_NAMES.
	## flip-api re-reads the parameter when the slot pool runs dry (register_trust
	## reconcile), so new kit slots become claimable with no task-definition change
	## and no restart. The plan diff is plain-text (the list is not a secret) —
	## review it before it applies. Also targets the flip-api task-role policy so
	## the first rollout (which adds the ssm:GetParameter read) is self-contained;
	## a no-op on every later run. Targeted on purpose: never run a full prod
	## `make apply` for this — it also applies whatever else is pending in the
	## plan, FL task-definition replacements (which kill a running job) included.
	##
	## NOT literally one resource: -target applies the target's whole dependency
	## closure, and the task-role policy references the bucket modules, whose
	## server-access logging targets aws_s3_bucket.flip_access_logs. So on an env whose
	## state predates that S3-logging hardening the first run ALSO creates
	## flip-access-logs-<subdomain> + its ACL/ownership (additive, 0 destroys). Seen on
	## stag 2026-07-14: 4 to add, 1 to change, 0 to destroy. Prod has no access-logs
	## bucket either — expect the same there. The plan prints and pauses for
	## confirmation before it applies (YES=1 skips the pause, matching add_fl_kits.sh).
	@echo "🎛️  Planning FL kit-slot parameter..."
	@$(_TF) plan -target=aws_ssm_parameter.fl_kit_slot_names \
		-target=aws_iam_role_policy.ecs_flip_api_task -out=plan-fl-kit-slots.tfplan
	@if [ "$(YES)" != "1" ]; then \
	  printf "   Apply this plan? [y/N] "; \
	  read -r answer; \
	  case "$$answer" in \
	    [Yy]|[Yy][Ee][Ss]) ;; \
	    *) echo "❌ Aborted — nothing applied (plan saved at plan-fl-kit-slots.tfplan)."; exit 1;; \
	  esac; \
	fi
	@$(_TF) apply plan-fl-kit-slots.tfplan
	@echo "✅ FL kit-slot parameter applied — new slots (if any) are claimable on the next trust registration."

.PHONY: add-fl-kits
add-fl-kits: ## Ensure N more claimable FL kit slots exist, end to end (NVFLARE; HUB=stag|prod)
	## Wraps scripts/add_fl_kits.sh: discover nets from S3 → ACTIVATE up to N spares
	## (kits already minted on every net but not yet in FL_KIT_SLOT_NAMES — no certs, no
	## upload) → MINT only the shortfall on every net (restore/fingerprint-verify each
	## net's CA workspace, additive S3 upload, never sync --delete) → append the activated
	## + minted names to FL_KIT_SLOT_NAMES in the env file. Then re-invokes make for
	## apply-fl-kit-slots so the sub-make re-reads the just-edited env file (this parse's
	## TF_VAR_FL_KIT_SLOT_NAMES predates the edit). N is a target ("N more live slots"),
	## not a mint count — spares are reused before any fresh mint, so a good kit is never
	## stranded and, when spares cover N, nothing is minted at all.
	@[ -n "$(N)" ] || { echo "❌ N is required, e.g. make add-fl-kits N=2 HUB=prod"; exit 1; }
	@$(_AWS_ENV) MAIN_ENV_FILE=$(MAIN_ENV_FILE) N=$(N) YES=$(YES) \
		FL_BACKEND=$(FL_BACKEND) ENV_NAME=$(ENV_CLASS) ./scripts/add_fl_kits.sh
	@$(MAKE) apply-fl-kit-slots HUB=$(HUB) YES=$(YES) || { \
	  echo "❌ Parameter apply failed AFTER the new slot names were appended to the env file"; \
	  echo "   (and any shortfall minted + uploaded)."; \
	  echo "   Recover with just: make apply-fl-kit-slots HUB=$(HUB)"; \
	  echo "   Do NOT re-run add-fl-kits — it would grow the pool by another N slots (activating"; \
	  echo "   more spares or minting higher-numbered kits)."; \
	  exit 1; }

.PHONY: destroy-cert
destroy-cert: ## Destroy only the ACM certificate (use with caution - will break HTTPS)
	@echo "💥 Destroying ACM certificate (Step 1/2)..."
	@$(_TF) destroy -target=aws_acm_certificate.flip -target=aws_acm_certificate_validation.flip -target=aws_route53_record.cert_validation
	@echo "✅ Certificate destroyed! Now run 'make plan' and 'make apply' to remove DNS validation."

.PHONY: refresh
refresh: ## Refresh Terraform state to sync with actual AWS resources
	@echo "🔄 Refreshing Terraform state..."
	@$(_TF) refresh
	@echo "✅ State refreshed!"


.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure (with environment file loaded)
	@echo "🛠️ Applying Terraform configuration..."
	@$(_TF) apply plan.tfplan

.PHONY: forward-trust
forward-trust: ## SSM port-forward all Trust web UIs and API swagger docs (Ctrl+C to stop)
	@bash scripts/forward-trust-all.sh

.PHONY: ssh-config
ssh-config: check-ssm-ready ## Update local .ssh/config for flip contexts (requires SSM plugin)
	@echo "⚙️  Configure .ssh/config for flip context..."
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; AWS_PROFILE=${AWS_PROFILE} terraform output
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; AWS_PROFILE=${AWS_PROFILE} uv run ./update_ssm_ssh_config.py --aws-profile ${AWS_PROFILE}
	@echo "✅ .ssh/config updated!"

.PHONY: update-env
update-env: ## Update environment file with Terraform outputs
	@echo "🔄 Updating environment file with infrastructure values..."
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; uv run ./update_env.py $(MAIN_ENV_FILE)

.PHONY: ansible-init
ansible-init: ## Initialize Ansible configuration on EC2 instances (if failed try again for 3 times)
	@echo "☁️🔗 Configure the Central Hub bastion and provision the Trust EC2"
	@[ -n "$(TRUST_DATA_VERSION)" ] || (echo "❌  trust/.data_version not found"; exit 1)
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@# --skip-tags omop-vocab,seed: the vocabulary load and the data seed need the
	@# trust's OMOP cluster credentials, which live in the kit file. This target
	@# runs BEFORE register-trusts in every deploy chain, so it has no KIT in scope
	@# (hence trust_num falling back to 1 above) and cannot supply them. Both run
	@# later from `make seed-trust-data KIT=<CODE>`, which does have them.
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; uv run ansible-playbook site.yml \
		--skip-tags omop-vocab,seed \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_backend=$(FL_BACKEND) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_num=$(or $(FL_KIT_SLOT_NUMBER),1) \
		-e trust_data_version=$(TRUST_DATA_VERSION)

.PHONY: provision-bastion
provision-bastion: ssh-config ## Install the minimal operator tooling on the Central Hub SSM bastion only
	@echo "🔧 Provisioning the Central Hub SSM bastion..."
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@uv run ansible-playbook site.yml \
		--tags bastion \
		--limit localhost,flip \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_backend=$(FL_BACKEND) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_data_version=$(TRUST_DATA_VERSION)
	@echo "✅ Central Hub SSM bastion provisioned."

# Cloud-trust targets are meaningless on a hub-only deployment — fail fast with
# a pointer to the on-prem flow instead of an obscure SSH/inventory error.
.PHONY: check-trust-ec2-enabled
check-trust-ec2-enabled:
	@[ "$(DEPLOY_TRUST_EC2)" != "false" ] || (echo "❌  DEPLOY_TRUST_EC2=false (hub-only deployment) — there is no cloud Trust EC2. On-prem trusts join via register-trusts and run 'sudo -E make -C trust up-trust' on their own host."; exit 1)

.PHONY: seed-trust-data
seed-trust-data: check-trust-ec2-enabled ssh-config ## Seed the Trust EC2's OMOP + Orthanc from the canonical dataset, then load the vocabulary (re-run after a data version bump; requires KIT=)
	@# SEED_PROJECTS / SEED_ORTHANC / SOURCE_TRUST thread through to the seed play (FLIP#1187):
	@#   make seed-trust-data KIT=GSTT HUB=stag SEED_PROJECTS="spleen_project prostate_project" SEED_ORTHANC=false
	@# FORCE_DICOM_VOCAB=1 reloads the DICOM vocabulary over a half-loaded one that reports
	@# itself present (the same escape trust/omop-db/Makefile has); off unless asked for.
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make seed-trust-data KIT=Trust_2 HUB=stag"; exit 1)
	@[ -n "$(FL_KIT_SLOT_NUMBER)" ] || (echo "❌  FL_KIT_SLOT_NUMBER not found in $(KIT_FILE_LABEL) — run register-trusts first"; exit 1)
	@[ -n "$(TRUST_DATA_VERSION)" ] || (echo "❌  trust/.data_version not found"; exit 1)
	@[ -n "$(OMOP_POSTGRES_PASSWORD_KIT)" ] || (echo "❌  OMOP_POSTGRES_PASSWORD not found in $(KIT_FILE_LABEL) — the seed and vocab-load plays need the cluster credentials"; exit 1)
	@[ -n "$(DATA_ACCESS_POSTGRES_PASSWORD_KIT)" ] || (echo "❌  DATA_ACCESS_POSTGRES_PASSWORD not found in $(KIT_FILE_LABEL) — omop-db's first start needs it to create the read-only role"; exit 1)
	@echo "🗄️  Seeding Trust EC2 (slot $(FL_KIT_SLOT_NUMBER)) from trust-data $(TRUST_DATA_VERSION)..."
	@uv run ansible-playbook site.yml \
		--tags data \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_num=$(FL_KIT_SLOT_NUMBER) \
		-e trust_data_version=$(TRUST_DATA_VERSION) \
		-e omop_postgres_user=$(OMOP_POSTGRES_USER_KIT) \
		-e omop_postgres_password=$(OMOP_POSTGRES_PASSWORD_KIT) \
		-e omop_postgres_db=$(OMOP_POSTGRES_DB_KIT) \
		-e data_access_postgres_password=$(DATA_ACCESS_POSTGRES_PASSWORD_KIT) \
		-e orthanc_username=$(ORTHANC_USERNAME_KIT) \
		-e orthanc_password=$(ORTHANC_PASSWORD_KIT) \
		$(if $(SEED_PROJECTS),-e seed_projects="$(SEED_PROJECTS)") \
		$(if $(SEED_ORTHANC),-e seed_orthanc=$(SEED_ORTHANC)) \
		$(if $(SOURCE_TRUST),-e source_trust=$(SOURCE_TRUST)) \
		$(if $(filter 1,$(FORCE_DICOM_VOCAB)),-e force_dicom_vocab=true) \
		-e vocab_s3_bucket=$(AICENTRE_BUCKET_NAME) \
		-e omop_db_tag=$(DOCKER_TAG)
	@echo "✅ Trust data seeding complete."

.PHONY: stage-fl-kit
stage-fl-kit: check-trust-ec2-enabled ssh-config ## Stage the FL participant kit for the trust's REGISTERED slot onto the Trust EC2 (requires KIT=)
	@# ansible-init runs BEFORE register-trusts in every deploy chain, so its kit
	@# staging falls back to trust_num=1 (no kit file exists yet to read the slot
	@# from). This target is the post-registration correction: it re-runs only the
	@# kit-staging plays (`--tags flkit`) with the slot the hub actually assigned,
	@# read from the kit file register-trusts wrote. deploy-trust depends on it so
	@# the fl-client always mounts its own slot's kit — without it, a host assigned
	@# Trust_2+ (re-registration, slot churn, second EC2 trust) would silently run
	@# with Trust_1's FL identity. Idempotent: the plays wipe-then-sync, so a
	@# re-run on a genuine Trust_1 host is a cheap no-op.
	@# trust_data_version is not used by the flkit plays but is asserted by the
	@# always-tagged inventory play, same as every site.yml invocation.
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make stage-fl-kit KIT=Trust_2 HUB=stag"; exit 1)
	@[ -n "$(FL_KIT_SLOT_NUMBER)" ] || (echo "❌  FL_KIT_SLOT_NUMBER not found in $(KIT_FILE_LABEL) — run register-trusts first"; exit 1)
	@[ -n "$(TRUST_DATA_VERSION)" ] || (echo "❌  trust/.data_version not found"; exit 1)
	@echo "🎫 Staging the $(FL_BACKEND) participant kit for slot Trust_$(FL_KIT_SLOT_NUMBER) onto the Trust EC2..."
	@uv run ansible-playbook site.yml \
		--tags flkit \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_backend=$(FL_BACKEND) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_num=$(FL_KIT_SLOT_NUMBER) \
		-e trust_data_version=$(TRUST_DATA_VERSION)
	@echo "✅ FL participant kit staged for slot Trust_$(FL_KIT_SLOT_NUMBER)."

.PHONY: deploy-centralhub
deploy-centralhub: ## Deploy Central Hub ECS services at the env branch tip (new task-def revisions) and publish the UI
	## Immutable deploy (FLIP#751). The tag is resolved INSIDE the recipe (not a Make-level
	## shell function) so the git fetch only runs when this target does; `cut -c1-7` pins the
	## abbreviation to exactly 7 chars to match CI's sha-tag truncation — `git rev-parse --short=7`
	## can grow beyond 7 when abbreviations become ambiguous, and the GHCR guard below
	## catches any residual mismatch loudly. Two phases: EVERY service's target image is
	## resolved and its GHCR manifest verified before ANY service is mutated, so a missing
	## tag (builds are path-filtered — a merge that didn't touch a service publishes no new
	## tag for it) aborts with nothing deployed. Non-ghcr.io registries skip the manifest
	## check (LZA ECR pull-through cache, FLIP#749). The app container is selected BY NAME —
	## never by index — matching the describe-tasks convention in TROUBLESHOOTING.md §1.8
	## and staying robust to sidecar/ordering changes; only its image tag is swapped before
	## registering the new revision. Task-def tags are carried over only when non-empty
	## (register-task-definition rejects an empty tags array). Pass TAG=sha-<short7> to
	## deploy a hotfix / feature-branch image (build it first via workflow_dispatch — see
	## AGENTS.md); revert with `make rollback-centralhub`.
	## FL quiesce warning (FLIP#770): an ECS replacement kills any in-flight training run
	## (fl-server run state is ephemeral) and strands its model/net BUSY. For now this is a
	## printed reminder only, not an enforced gate — enable Deployment Mode first (it pauses
	## FL job pickup) and wait for any running job to finish; GET /fl/quiesce reports both.
	## On HUB=prod the reminder also asks for interactive confirmation; stag proceeds
	## without prompting so scripted staging deploys stay non-interactive.
	## A release apply pins `repo:v<X.Y.Z>@sha256:…` (FLIP#1283) — the release name is still
	## there to swap TAG onto, so the digest is stripped and the deploy proceeds; only a
	## digest-ONLY reference (no tag at all) has nothing to replace and is refused.
	@set -e; \
	export AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION); \
	base_ref() { \
	  _img="$$1"; _svc="$$2"; _base="$$_img"; \
	  case "$$_img" in \
	  *@*) \
	    _base="$${_img%@*}"; \
	    case "$$_base" in \
	    *:*) ;; \
	    *) echo "❌ $$_svc: digest-only image '$$_img' — cannot swap a tag onto it" >&2; return 1;; \
	    esac;; \
	  esac; \
	  printf '%s' "$$_base"; \
	}; \
	echo "⚠️  FL quiesce reminder (FLIP#770): replacing fl-server-net-1 kills any in-flight"; \
	echo "   training run and strands its model at TRAINING_STARTED with its net stuck BUSY."; \
	echo "   Before deploying: enable Deployment Mode (Admin → Deployments) — it pauses FL job"; \
	echo "   pickup, so queued jobs hold — and wait for any running job to finish."; \
	echo "   GET $(CENTRAL_HUB_API_URL)/fl/quiesce reports both (deployment_mode + fl_quiesced)."; \
	if [ "$(HUB)" = "prod" ]; then \
	  printf "   Are you sure you want to continue? [y/N] "; \
	  read -r answer; \
	  case "$$answer" in \
	    [Yy]|[Yy][Ee][Ss]) ;; \
	    *) echo "❌ Aborted — nothing has been deployed."; exit 1;; \
	  esac; \
	fi; \
	TAG="$(TAG)"; \
	if [ -z "$$TAG" ]; then \
	  echo "🔎 Resolving deploy tag from the tip of $(DEPLOY_GIT_REF)..."; \
	  git fetch -q origin; \
	  sha=$$(git rev-parse $(DEPLOY_GIT_REF)) || { echo "❌ Cannot resolve $(DEPLOY_GIT_REF)"; exit 1; }; \
	  TAG="sha-$$(printf '%s' "$$sha" | cut -c1-7)"; \
	fi; \
	printf '%s' "$$TAG" | grep -Eq '^(sha-[0-9a-f]{7}|v[0-9]+\.[0-9]+\.[0-9]+)$$' || { \
	  echo "❌ TAG '$$TAG' is not an immutable tag: sha-<short7> (7 hex chars) or a release v<X.Y.Z>."; \
	  echo "   deploy-centralhub only deploys CI-published immutable tags — a mutable tag here would"; \
	  echo "   defeat the revision audit trail. Omit TAG= to deploy the tip of $(DEPLOY_GIT_REF)."; \
	  exit 1; }; \
	echo "🚀 Deploying Central Hub ECS services in $(ECS_CLUSTER) at $$TAG..."; \
	for service in $(CENTRALHUB_ECS_SERVICES); do \
	  current_td=$$($(_AWS_ENV) aws ecs describe-services --cluster $(ECS_CLUSTER) --services $$service \
	    --query 'services[0].taskDefinition' --output text); \
	  if [ -z "$$current_td" ] || [ "$$current_td" = "None" ]; then \
	    echo "❌ $$service not found in cluster $(ECS_CLUSTER)"; exit 1; \
	  fi; \
	  td_json=$$($(_AWS_ENV) aws ecs describe-task-definition --task-definition "$$current_td"); \
	  image=$$(printf '%s' "$$td_json" | jq -r --arg name "$$service" \
	    '.taskDefinition.containerDefinitions[] | select(.name == $$name).image'); \
	  [ -n "$$image" ] || { echo "❌ $$service: no container named '$$service' in $$current_td"; exit 1; }; \
	  base_image=$$(base_ref "$$image" "$$service") || exit 1; \
	  new_image="$${base_image%:*}:$$TAG"; \
	  case "$$new_image" in \
	  ghcr.io/*) \
	    repo=$${new_image#ghcr.io/}; repo=$${repo%:*}; \
	    token=$$(curl -fsSL "https://ghcr.io/token?scope=repository:$$repo:pull" | jq -r '.token // empty'); \
	    [ -n "$$token" ] || { \
	      echo "❌ GHCR token fetch failed (network/registry problem) — cannot verify $$new_image."; \
	      echo "   This is NOT a missing build; retry once GHCR is reachable. Nothing has been deployed."; \
	      exit 1; }; \
	    accept='application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'; \
	    accept="$$accept, application/vnd.docker.distribution.manifest.list.v2+json"; \
	    accept="$$accept, application/vnd.docker.distribution.manifest.v2+json"; \
	    status=$$(curl -sSI -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $$token" \
	      -H "Accept: $$accept" "https://ghcr.io/v2/$$repo/manifests/$$TAG") || status=000; \
	    case "$$status" in \
	    200) echo "   ✓ $$service: $$new_image verified in GHCR";; \
	    404) \
	      case "$$service" in \
	        flip-api) wf=docker_build_flip_api.yml; is_fl=false;; \
	        *) wf=fl-docker-build-$(FL_BACKEND).yml; is_fl=true;; \
	      esac; \
	      echo "❌ $$new_image not found in GHCR."; \
	      echo "   Builds are path-filtered (a merge that didn't touch this service publishes no new"; \
	      echo "   tag for it), and flip-api's is additionally test-gated so it may still be running:"; \
	      echo "     gh run list --workflow=$$wf"; \
	      if [ "$$is_fl" = true ]; then \
	        echo "   $$wf builds fl-server, fl-api, fl-client and fl-base together, so their sha-<short7>"; \
	        echo "   tags publish as one set — a 404 here means that whole backend build did not run for"; \
	        echo "   this commit, not that a single image within the multi-image build is missing."; \
	      fi; \
	      echo "   To build a missing / branch / hotfix image: gh workflow run $$wf --ref <branch>"; \
	      echo "   Nothing has been deployed."; \
	      exit 1;; \
	    *) \
	      echo "❌ GHCR manifest check for $$new_image returned HTTP $$status — a registry/auth/"; \
	      echo "   network problem, not a missing build. Do not dispatch rebuilds; retry the deploy."; \
	      echo "   Nothing has been deployed."; \
	      exit 1;; \
	    esac;; \
	  *) echo "   ⚠️  $$new_image is not on ghcr.io — skipping the registry existence check (FLIP#749)";; \
	  esac; \
	done; \
	deployed=""; tmp_td=""; \
	trap 'rm -f $$tmp_td' EXIT; \
	warn_mixed() { \
	  [ -n "$$deployed" ] || return 0; \
	  echo ""; \
	  echo "   ============================== MIXED STATE =============================="; \
	  echo "   ⚠️  PARTIAL DEPLOY — some services were already repointed this run before"; \
	  echo "       this failure; the Central Hub now runs a mix of old and new revisions."; \
	  echo "       Already repointed:$$deployed"; \
	  echo "       → Fix the error above and re-run 'make deploy-centralhub' to converge."; \
	  echo "       → Do NOT 'make rollback-centralhub' blindly: it would also downgrade the"; \
	  echo "         services that deployed cleanly this run."; \
	  echo "   ========================================================================="; \
	  echo ""; \
	}; \
	for service in $(CENTRALHUB_ECS_SERVICES); do \
	  current_td=$$($(_AWS_ENV) aws ecs describe-services --cluster $(ECS_CLUSTER) --services $$service \
	    --query 'services[0].taskDefinition' --output text); \
	  td_json=$$($(_AWS_ENV) aws ecs describe-task-definition --task-definition "$$current_td" --include TAGS); \
	  image=$$(printf '%s' "$$td_json" | jq -r --arg name "$$service" \
	    '.taskDefinition.containerDefinitions[] | select(.name == $$name).image'); \
	  [ -n "$$image" ] || { echo "❌ $$service: no container named '$$service' in $$current_td"; exit 1; }; \
	  new_image=$$(base_ref "$$image" "$$service") || { warn_mixed; exit 1; }; \
	  new_image="$${new_image%:*}:$$TAG"; \
	  jq_prog='.taskDefinition.containerDefinitions |= map(if .name == $$name then .image = $$image else . end)'; \
	  jq_prog="$$jq_prog | (.taskDefinition | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,"; \
	  jq_prog="$$jq_prog .compatibilities, .registeredAt, .registeredBy, .deregisteredAt))"; \
	  jq_prog="$$jq_prog + (if ((.tags // []) | length) > 0 then {tags: .tags} else {} end)"; \
	  tmp_td=$$(mktemp); \
	  printf '%s' "$$td_json" | jq --arg name "$$service" --arg image "$$new_image" "$$jq_prog" > $$tmp_td; \
	  if ! new_td_arn=$$($(_AWS_ENV) aws ecs register-task-definition --cli-input-json file://$$tmp_td \
	      --query 'taskDefinition.taskDefinitionArn' --output text); then \
	    echo "❌ $$service: register-task-definition failed."; \
	    warn_mixed; \
	    exit 1; \
	  fi; \
	  rm -f $$tmp_td; tmp_td=""; \
	  echo "   ↻ $$service: $${current_td##*/} → $${new_td_arn##*/} ($$new_image)"; \
	  if ! ( $(_AWS_ENV) aws ecs update-service --cluster $(ECS_CLUSTER) --service $$service \
	      --task-definition "$$new_td_arn" \
	      --query 'service.serviceName' --output text >/dev/null ); then \
	    echo "❌ $$service: update-service failed — deregistering the just-registered $${new_td_arn##*/}"; \
	    echo "   so the latest-ACTIVE lookups in ecs_services.tf keep tracking the running revision."; \
	    $(_AWS_ENV) aws ecs deregister-task-definition --task-definition "$$new_td_arn" >/dev/null \
	      || echo "   ⚠️  deregister failed too — deregister $${new_td_arn##*/} manually, or the next terraform apply will adopt it."; \
	    warn_mixed; \
	    exit 1; \
	  fi; \
	  deployed="$$deployed $$service"; \
	done
	@echo "⏳ Waiting for Central Hub ECS services to stabilise..."
	@$(_AWS_ENV) AWS_REGION=$(AWS_REGION) aws ecs wait services-stable \
	  --cluster $(ECS_CLUSTER) \
	  --services $(CENTRALHUB_ECS_SERVICES) || { \
	  echo "❌ Services did not stabilise — ECS keeps retrying the new deployment in the background"; \
	  echo "   (no circuit breaker). Inspect:"; \
	  echo "     aws ecs describe-services --cluster $(ECS_CLUSTER) --services $(CENTRALHUB_ECS_SERVICES) --query 'services[].events[:5]'"; \
	  echo "   Revert with: make rollback-centralhub HUB=$(HUB)"; \
	  exit 1; }
	@$(MAKE) deploy-ui HUB=$(HUB)
	@echo "✅ Central Hub ECS services and UI deployed."

.PHONY: rollback-centralhub
rollback-centralhub: ## Repoint each Central Hub ECS service at its previous ACTIVE task-definition revision
	## Reverts the last deploy-centralhub in seconds — no rebuild, no env-file edit. Each
	## service moves to the highest ACTIVE revision below its current one (CLI- and
	## Terraform-registered revisions both count), then the rolled-away revision is
	## DEREGISTERED so the latest-ACTIVE lookups in ecs_services.tf converge back to the
	## running revision — without this, the next `terraform apply` would re-adopt the very
	## revision just rolled back from (its max() tracks latest ACTIVE, not what runs).
	## Deregistered revisions stay describable for forensics. If the rolled-away revision
	## is Terraform's own (a rollback straight after an apply), the next apply re-registers
	## the family — that is the documented bootstrap-tag path; re-run deploy-centralhub
	## after it. The UI is NOT rolled back — re-run `make deploy-ui` from the matching
	## commit if the UI must move too.
	## On NVFLARE it first empties the fl-server's local/ on EFS and re-syncs the kit
	## (scripts/reset-fl-server-local.sh, the efs-provision-certs task, a minute or two):
	## since FLIP#1390 the fl-server writes local/flip__p_resources.json, and an older
	## fl-server image cannot load it and fails to start. If that step fails nothing is
	## rolled back. SKIP_FL_LOCAL_RESET=1 skips it, for when the file is already gone or
	## the revision rolled back to is FLIP#1390 or later (it rewrites the file at start).
	@set -e; \
	unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; export AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION); \
	fl_server="$(firstword $(filter fl-server-net-%,$(CENTRALHUB_ECS_SERVICES)))"; \
	if [ "$(FL_BACKEND)" = "nvflare" ] && [ -n "$$fl_server" ] && [ "$(SKIP_FL_LOCAL_RESET)" != "1" ]; then \
	  ./scripts/reset-fl-server-local.sh $(ECS_CLUSTER) "$$fl_server" || { \
	    echo "   Nothing has been rolled back. An fl-server image older than FLIP#1390 fails to start"; \
	    echo "   while local/flip__p_resources.json is on EFS. Fix the step above and re-run, or remove"; \
	    echo "   the file yourself (fl-services/nvflare/fl-server/README.md) and re-run with"; \
	    echo "   SKIP_FL_LOCAL_RESET=1."; \
	    exit 1; }; \
	fi; \
	echo "⏪ Rolling back Central Hub ECS services in $(ECS_CLUSTER)..."; \
	for service in $(CENTRALHUB_ECS_SERVICES); do \
	  current_td=$$($(_AWS_ENV) aws ecs describe-services --cluster $(ECS_CLUSTER) --services $$service \
	    --query 'services[0].taskDefinition' --output text); \
	  if [ -z "$$current_td" ] || [ "$$current_td" = "None" ]; then \
	    echo "❌ $$service not found in cluster $(ECS_CLUSTER)"; exit 1; \
	  fi; \
	  family=$${current_td##*/}; family=$${family%%:*}; rev=$${current_td##*:}; \
	  arns=$$($(_AWS_ENV) aws ecs list-task-definitions --family-prefix $$family --status ACTIVE --sort DESC \
	    --query 'taskDefinitionArns' --output json); \
	  jq_prev='map(select(contains("/" + $$family + ":")))'; \
	  jq_prev="$$jq_prev"' | map(select((split(":") | last | tonumber) < $$rev)) | first // empty'; \
	  previous=$$(printf '%s' "$$arns" | jq -r --arg family "$$family" --argjson rev "$$rev" "$$jq_prev"); \
	  if [ -z "$$previous" ]; then \
	    echo "❌ $$service: no ACTIVE revision below $${current_td##*/} to roll back to"; exit 1; \
	  fi; \
	  echo "   ⏪ $$service: $${current_td##*/} → $${previous##*/}"; \
	  $(_AWS_ENV) aws ecs update-service --cluster $(ECS_CLUSTER) --service $$service \
	    --task-definition "$$previous" \
	    --query 'service.serviceName' --output text >/dev/null; \
	  if ! ( $(_AWS_ENV) aws ecs deregister-task-definition --task-definition "$$current_td" \
	      --query 'taskDefinition.status' --output text >/dev/null ); then \
	    echo "❌ $$service rolled back to $${previous##*/}, but deregistering $${current_td##*/} failed —"; \
	    echo "   it is still the latest ACTIVE revision, so the next terraform apply would re-adopt"; \
	    echo "   it. Deregister manually:"; \
	    echo "     aws ecs deregister-task-definition --task-definition $$current_td"; \
	    exit 1; \
	  fi; \
	  echo "   🗑  $$service: deregistered $${current_td##*/} (stays describable, no longer ACTIVE)"; \
	done
	@echo "⏳ Waiting for Central Hub ECS services to stabilise..."
	@$(_AWS_ENV) AWS_REGION=$(AWS_REGION) aws ecs wait services-stable \
	  --cluster $(ECS_CLUSTER) \
	  --services $(CENTRALHUB_ECS_SERVICES) || { \
	  echo "❌ Services did not stabilise after the rollback — inspect:"; \
	  echo "     aws ecs describe-services --cluster $(ECS_CLUSTER) --services $(CENTRALHUB_ECS_SERVICES) --query 'services[].events[:5]'"; \
	  exit 1; }
	@echo "✅ Central Hub ECS services rolled back."
	@echo "⚠️  The flip-ui bundle was NOT rolled back — if the reverted release also changed the"
	@echo "   UI, re-run 'make deploy-ui HUB=$(HUB)' from the matching commit to avoid a"
	@echo "   frontend/backend mismatch."

.PHONY: deploy-ui
deploy-ui: ## Build flip-ui from the working tree and deploy it to S3 + invalidate CloudFront
	@echo "🖼️  Building flip-ui from working tree..."
	@# Uses build:deploy (vite build only). The type-checked `npm run build` fails
	@# on pre-existing TypeScript errors in the repo — tracked separately.
	@cd ../../../flip-ui && npm ci && npm run build:deploy
	@echo "🔧 Generating window.js from $(MAIN_ENV_FILE)..."
	@cd ../../../flip-ui && sh scripts/generate-window-js.sh > dist/js/window.js
	@echo "📦 Syncing static assets to the deployed UI bucket..."
	@# Hashed bundles are content-addressed and safe to cache forever.
	@# index.html and window.js must never be cached long — they reference the hashed bundles.
	@UI_BUCKET=$$($(_AWS_ENV) terraform output -raw FlipUiBucketName); \
	[ -n "$$UI_BUCKET" ] || { echo "❌ Terraform output FlipUiBucketName is empty"; exit 1; }; \
	echo "   s3://$$UI_BUCKET/"; \
	$(_AWS_ENV) aws s3 sync ../../../flip-ui/dist/ s3://$$UI_BUCKET/ \
		--delete \
		--cache-control "public, max-age=31536000, immutable" \
		--exclude "index.html" --exclude "js/window.js" --exclude "ark_demo/*"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/index.html s3://$$UI_BUCKET/index.html \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "text/html; charset=utf-8"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/js/window.js s3://$$UI_BUCKET/js/window.js \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "application/javascript; charset=utf-8"
	@# There is no workload distribution to invalidate (the networking account's edge
	@# distribution serves the bucket) and no cross-account
	@# invalidation permission -- BY DESIGN (FLIP#749): the hashed bundles are immutable
	@# and index.html/window.js carry no-cache metadata, so a deploy is visible on the
	@# next index.html fetch with no invalidation at all.
	@echo "🧹 No CloudFront invalidation (the edge distribution honours the Cache-Control metadata)."
	@echo "✅ deploy-ui complete."

.PHONY: deploy-ark-demo
deploy-ark-demo: ## Build the public Ark+ demo SPA from the working tree and deploy it to S3 (ark_demo/ prefix)
	@# Refuse to publish the demo into an environment that hosts none (stag leaves
	@# DEMO_ASSETS_BUCKET_NAME unset): served under the default behaviour, it would run
	@# unauthenticated on the same origin as real Cognito tokens (FLIP#794 review). The
	@# /ark_demo/* behaviour and its CSP live on the edge distribution (aicentre-lza-iac).
	@if [ -z "$$DEMO_ASSETS_BUCKET_NAME" ]; then \
		echo "❌ DEMO_ASSETS_BUCKET_NAME is not set for this environment, which hosts no public demo."; \
		echo "   Set it (and apply), and give the edge distribution its /ark_demo/* behaviour, first."; \
		exit 1; \
	fi
	@echo "🖼️  Building flip-ui Ark+ demo bundle from working tree..."
	@# npm run build:demo's postbuild hook regenerates dist/js/window.js itself
	@# (scripts/generate-demo-window-js.sh) — no separate window.js step needed,
	@# unlike deploy-ui.
	@cd ../../../flip-ui && npm ci && npm run build:demo
	@echo "📦 Syncing demo assets to the deployed UI bucket (ark_demo/ prefix)..."
	@# Same cache-control discipline as deploy-ui: hashed assetsDir chunks
	@# (dist/static/... for the demo build — see the Vite assetsDir note in
	@# ../README.md) are content-addressed and safe to cache forever; index.html
	@# and window.js must never be cached long since they reference the hashed
	@# bundle. --delete is scoped to this sync's destination (the ark_demo/
	@# prefix), so it never touches the real app's objects at the bucket root —
	@# see deploy-ui's own --exclude "ark_demo/*" for the other half of that
	@# separation.
	@UI_BUCKET=$$($(_AWS_ENV) terraform output -raw FlipUiBucketName); \
	[ -n "$$UI_BUCKET" ] || { echo "❌ Terraform output FlipUiBucketName is empty"; exit 1; }; \
	echo "   s3://$$UI_BUCKET/ark_demo/"; \
	$(_AWS_ENV) aws s3 sync ../../../flip-ui/dist/ s3://$$UI_BUCKET/ark_demo/ \
		--delete \
		--cache-control "public, max-age=31536000, immutable" \
		--exclude "index.html" --exclude "js/window.js"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/index.html s3://$$UI_BUCKET/ark_demo/index.html \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "text/html; charset=utf-8"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/js/window.js s3://$$UI_BUCKET/ark_demo/js/window.js \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "application/javascript; charset=utf-8"
	@echo "✅ deploy-ark-demo complete."

# Identity of the trust kit being deployed/provisioned — a trust CODE that
# selects its kit file trust/.env.<KIT>.<env> and its trust-local data dirs.
# REQUIRED on the command line for trust targets (no default, so a prod op
# can't silently target the wrong trust). Scaffold one with `make new-trust`.
# FL participant kit slot the trust's fl-client mounts — names the
# services/<slot>/ dir in the S3 participant-kit bundle and under /opt/flip.
# Read from the trust's kit file (the single source of truth: the kit file's
# FL_KIT_SLOT is also what trust/Makefile mounts), not duplicated here.
#
# Both are DEFERRED (`=`, not `:=`) on purpose. A simply-expanded assignment is evaluated once
# when the Makefile is parsed, which is before any target runs — and `register-trusts` is what
# writes these values into the kit file. Inside a single `make full-deploy`, `register-trusts`
# and `deploy-trust` are prerequisites of the same make process, so a parse-time snapshot hands
# the staging targets whatever the kit file held *before* registration: empty on a first
# deployment, and the previously claimed slot after a delete-then-re-register (which claims the
# next free slot rather than the old one) — the silent wrong-slot stage the guards below exist
# to prevent. Deferred, each reference re-reads the file at recipe-expansion time and therefore
# sees what register-trusts just wrote. The repeated $(shell) is a `sed` over one small file.
FL_KIT_SLOT = $(shell sed -n 's/^FL_KIT_SLOT=//p' $(KIT_FILE) 2>/dev/null)
FL_KIT_SLOT_NUMBER = $(shell sed -n 's/^FL_KIT_SLOT_NUMBER=//p' $(KIT_FILE) 2>/dev/null)

# Trust-local OMOP DB / Orthanc credentials from the kit — passed to the seed and
# vocab-load plays (the throwaway containers they start on the data directories
# must be initialised with the same credentials the deployed stack will use).
OMOP_POSTGRES_USER_KIT := $(shell sed -n 's/^OMOP_POSTGRES_USER=//p' $(KIT_FILE) 2>/dev/null)
OMOP_POSTGRES_PASSWORD_KIT := $(shell sed -n 's/^OMOP_POSTGRES_PASSWORD=//p' $(KIT_FILE) 2>/dev/null)
OMOP_POSTGRES_DB_KIT := $(shell sed -n 's/^OMOP_POSTGRES_DB=//p' $(KIT_FILE) 2>/dev/null)
DATA_ACCESS_POSTGRES_PASSWORD_KIT := $(shell sed -n 's/^DATA_ACCESS_POSTGRES_PASSWORD=//p' $(KIT_FILE) 2>/dev/null)
ORTHANC_USERNAME_KIT := $(shell sed -n 's/^ORTHANC_USERNAME=//p' $(KIT_FILE) 2>/dev/null)
ORTHANC_PASSWORD_KIT := $(shell sed -n 's/^ORTHANC_PASSWORD=//p' $(KIT_FILE) 2>/dev/null)

# The data version read from the canonical trust/.data_version — a git tag on the
# aicentreflip/trust-data dataset that pins OMOP and Orthanc mock data together. It
# governs both the local seed (`make -C trust ensure-seeded`) and the Ansible plays,
# which fetch the canonical tables and DICOM sets at that tag.
TRUST_DATA_VERSION := $(shell cat ../../../trust/.data_version 2>/dev/null | tr -d ' \n\r\t')

# seed-trust-data is NOT a prerequisite: prerequisites run before the recipe, i.e. before
# the stack below is stopped, and the seed play starts a throwaway omop-db on the same data
# directory the running omop-db has open — Postgres takes the foreign PID in postmaster.pid
# for stale and runs crash recovery over a live database (#1190 review). So the seed runs in
# the recipe, between down-trust-ec2 and up-trust-ec2; the play itself also refuses while any
# omop-db is up, for the standalone `make seed-trust-data`. SEED_PROJECTS / SEED_ORTHANC /
# SOURCE_TRUST given on this command line reach the sub-make through MAKEFLAGS.
.PHONY: deploy-trust
deploy-trust: check-trust-ec2-enabled ssh-config update-env stage-fl-kit ## Deploy trust relationships after central hub is deployed
	@echo "🔍 Checking trust images (tag: $(DOCKER_TAG))..."
	@missing=false; \
	for image in $(TRUST_IMAGES); do \
	  if ! docker manifest inspect "$$image" >/dev/null 2>&1; then \
	    echo "   ⚠️  Missing: $$image"; \
	    missing=true; \
	  fi; \
	done; \
	if [ "$$missing" = "true" ]; then \
	  echo "🔨 Images missing — triggering builds and waiting 2 minutes..."; \
	  $(MAKE) trigger-builds; \
	  sleep 120; \
	else \
	  echo "   ✅ All trust images found."; \
	fi
	@echo "👷 Create docker context if not exists, pointing to flip-trust to ssh://flip-trust"
	@docker context create flip-trust --docker "host=ssh://flip-trust" || echo "   Context 'flip-trust' already exists"
	@echo "🐳 Deploying containers to Trust EC2 ..."
	docker --context flip-trust swarm init || echo "   Swarm already initialized"
	@echo "🔧 Creating Docker networks on Trust EC2..."
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../ create-networks
	@echo "🧹 Stopping existing stack for KIT=$(KIT) and pruning unused images..."
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../trust down-trust-ec2 KIT=$(KIT) HUB=${HUB} 2>/dev/null || true
	@docker --context flip-trust image prune -af 2>/dev/null || true
	@echo "🗄️  Seeding trust data while the stack is down..."
	$(MAKE) seed-trust-data KIT=$(KIT) HUB=$(HUB)
	@echo "🚀 Deploying Trust services..."
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../ up-trust-ec2 KIT=$(KIT) HUB=${HUB}
	@echo "✅ Trust deployment complete!"

# Move the EC2 trust to a release, data intact (FLIP#1204). deploy-trust above is the
# FIRST-INSTALL verb: its seed-trust-data step (run while the stack is down) re-seeds
# OMOP/Orthanc on the host and its up-trust-ec2 runs xnat-reset over ssh. This target has none of that —
# it drives trust/Makefile's upgrade-trust through the same ssh docker context (pull the
# target tag, recreate what changed, redeploy XNAT without reset, converge its config).
# TAG defaults to the release the hub runs; FORCE=1 allows a downgrade; YES=1 skips the
# confirmation. The kit file read is this workstation's trust/.env.<KIT>.<env>, exactly as
# deploy-trust evaluates compose here against the remote daemon. NUM_AVAILABLE_GPUS=0 pins the
# fl-client to CPU as up-trust-ec2 does (the t3.xlarge has no GPU): on the command line it beats
# a template kit's 1, dropping the GPU overlay and reaching compose's env over the --env-file.
.PHONY: upgrade-trust-ec2
upgrade-trust-ec2: check-trust-ec2-enabled ssh-config ## Move the EC2 trust to a release tag without re-seeding data (FLIP#1204)
	@echo "👷 Create docker context if not exists, pointing to flip-trust to ssh://flip-trust"
	@docker context create flip-trust --docker "host=ssh://flip-trust" || echo "   Context 'flip-trust' already exists"
	@echo "⬆️  Upgrading Trust services on EC2 (KIT=$(KIT))..."
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../trust upgrade-trust KIT=$(KIT) HUB=${HUB} TAG=$(TAG) FL_TAG=$(FL_TAG) FORCE=$(FORCE) YES=$(YES) ALLOW_CHECKOUT_DRIFT=$(ALLOW_CHECKOUT_DRIFT) NUM_AVAILABLE_GPUS=0
	@echo "✅ Trust upgrade complete!"

.PHONY: provision-local-trust
provision-local-trust: ## Provision the on-prem (local) trust host: install Docker + deps, stage the FL kit
	## Run this ON the trust host. Configures the machine via Ansible (local connection) and
	## downloads the FL participant kit from S3. Requires ANSIBLE_BECOME_PASS in the environment.
	## The S3 kit download needs AWS credentials (fine for FLIP-team testing); for real on-prem
	## trusts the participant kit is delivered to the operator out-of-band.
	@echo "🔧 Provisioning the on-premises Trust host (this machine)..."
	@uv sync
	@uv run ansible-galaxy install -r $(ONPREM_ANSIBLE_DIR)/requirements.yml
	@if [ -z "$$ANSIBLE_BECOME_PASS" ]; then \
		echo ""; \
		echo "❌  ANSIBLE_BECOME_PASS is not set. Run this in your fish terminal first:"; \
		echo ""; \
		echo "     set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')"; \
		echo ""; \
		echo "   Then re-run this make target."; \
		echo ""; \
		exit 1; \
	fi
	@echo "🔐 Authenticating sudo and creating temporary NOPASSWD entry..."
	@CURR_USER=$$(id -un); \
		echo "$$ANSIBLE_BECOME_PASS" | sudo -S sh -c \
			"echo '$$CURR_USER ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/flip-ansible-local && chmod 440 /etc/sudoers.d/flip-ansible-local" \
			2>/dev/null || { echo "❌  sudo authentication failed — check your password."; exit 1; }; \
		echo "✅ Authenticated. Running Ansible playbook..."; \
		trap 'sudo -n rm -f /etc/sudoers.d/flip-ansible-local' EXIT INT TERM; \
		ANSIBLE_BECOME_PASS= uv run ansible-playbook \
			-i "localhost," \
			-e ansible_connection=local \
			-e ansible_python_interpreter=/usr/bin/python3 \
			-e fl_backend=$(FL_BACKEND) \
			$(ONPREM_ANSIBLE_DIR)/onprem.yml; \
		PLAY_RC=$$?; \
		exit $$PLAY_RC
	@echo "📦 Downloading the FL participant kit for the on-prem trust from S3..."
ifeq ($(FL_BACKEND),nvflare)
	@[ -n "$(FL_KIT_SLOT)" ] || (echo ""; echo "❌  FL_KIT_SLOT not found — is $(KIT_FILE_LABEL) present with FL_KIT_SLOT set?"; echo ""; exit 1)
	@TRUST_KIT_DIR=/tmp/$(FL_KIT_SLOT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR && \
	  $(_AWS_ENV) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FL_KIT_DATE)/net-1/services/$(FL_KIT_SLOT)/ \
	    $$TRUST_KIT_DIR && \
	  echo "" && \
	  echo "  # Deploy the $(FL_KIT_SLOT) NVFLARE participant kit (staged at $$TRUST_KIT_DIR):" && \
	  echo "  # \$$FL_KIT_DIR is the kit root from $(KIT_FILE_LABEL) (default /opt/flip/fl-kit):" && \
	  echo "  sudo mkdir -p /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)/startup /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)/local /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)/transfer" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/startup/ /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)/startup/" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/local/ /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)/local/" && \
	  echo "  # fl-client now runs as a non-root uid (GHSA-8465) — hand the rsync'd kit back to" && \
	  echo "  # this shell's own user so the container can read/write it:" && \
	  echo "  sudo chown -R \$$(id -u):\$$(id -g) /opt/flip/fl-kit/net-1/services/$(FL_KIT_SLOT)" && \
	  echo "✅ $(FL_KIT_SLOT) NVFLARE kit staged"
else
	@TRUST_KIT_DIR=/tmp/$(KIT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR/net-1 && \
	  $(_AWS_ENV) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flower-participant-kits/$(FL_KIT_DATE)/net-1/ \
	    $$TRUST_KIT_DIR/net-1/ && \
	  echo "" && \
	  echo "  # Deploy the Flower participant kit (staged at $$TRUST_KIT_DIR):" && \
	  echo "  # \$$FL_KIT_DIR is the kit root from $(KIT_FILE_LABEL) (default /opt/flip/fl-kit);" && \
	  echo "  # the compose mounts \$$FL_KIT_DIR/net-1/{certificates,keys}:" && \
	  echo "  sudo mkdir -p /opt/flip/fl-kit/net-1" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/net-1/ /opt/flip/fl-kit/net-1/" && \
	  echo "✅ Flower kit staged"
endif
	@echo ""
	@echo "✅ On-premises Trust host provisioned!"
	@echo ""
	@echo "⚠️  REMAINING MANUAL STEPS:"
	@echo "   1. Deploy the staged FL kit into /opt/flip (run the commands printed above)."
	@echo "   2. Start the trust stack (sudo: the login user is deliberately not in the docker group):"
	@echo "      cd ../../.. && sudo -E make -C trust up-trust KIT=$(KIT) HUB=$(HUB)"

.PHONY: down
down:
	@echo "🛑 Stopping all containers..."
	DOCKER_CONTEXT=flip $(MAKE) -C ../../../ down
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../ down
	@echo "✅ Containers stopped and removed"

.PHONY: status
status: ## Check deployment status
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; uv run ./check_status.py

check:
	@echo "🔍 Checking Terraform state..."
	@$(_TF) state list
	@echo ""
	@$(_TF) output
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; uv run ./check_status.py || true


.PHONY: destroy
destroy: ## Destroy ephemeral infrastructure (EC2, RDS) - preserves Cognito, Secrets, S3
	@echo ""
	@echo "⚠️  SELECTIVE DESTRUCTION - PRESERVING:"
	@echo "   ✓ Cognito user pool and identities"
	@echo "   ✓ AWS Secrets Manager (FLIP_API secret)"
	@echo "   ✓ S3 buckets (FLIP + AI Centre data)"
	@echo "   ✓ ACM certificates and Route53 records"
	@echo "   ✓ The platform's VPC (never this stack's)"
	@echo ""
	@echo "🗑️  DESTROYING:"
	@echo "   • EC2 instances (flip, trust)"
	@echo "   • RDS database (PostgreSQL)"
	@echo "   • Security groups"
	@echo "   • IAM roles and policies"
	@echo "   • CloudWatch logs"
	@echo ""
	@bash -c 'read -p "Type '\''yes'\'' to confirm destruction: " confirm && [ "$$confirm" = "yes" ] || { echo "Aborted."; exit 1; }'
	@echo ""
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; bash scripts/destroy-selective.sh


.PHONY: help
help: ## Show this help message
	@echo "FLIP AWS Deployment Commands:"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$0, $$1, $$2}'

.PHONY: generate-internal-service-key
generate-internal-service-key: ## Generate a new internal service key (fl-server → flip-api auth)
	@echo "🔑 Generating internal service key..."
	@$(MAKE) -C ../../../flip-api generate-internal-service-key ENV_FILE=$(abspath $(MAIN_ENV_FILE))

.PHONY: register-trusts
register-trusts: ## Register CODE-named trust kit(s) on the hub and write trust/.env.<CODE>.<env>
	## Mirrors the dev `make register-trusts` (same register_trust CLI + kit-writer); the only
	## difference is transport — a one-off ECS task + SSM SecureString handoff (not CloudWatch).
	## Idempotent: an already-registered trust returns a metadata-only kit (no creds; the hub
	## stores only the SHA-256 hash), so re-running is safe. Delivery is command-driven
	## (deploy-trust pushes to the EC2 host; package-onprem-trust-kit tarballs for on-prem).
	##   KIT=<CODE> → register that one kit (trust/.env.<CODE>.<env>)
	##   (no KIT)   → register every live trust/.env.*.<env> kit (an old-named one only with KIT=);
	##                 none is an error unless DEPLOY_TRUST_EC2=false (a hub-only deploy may register later)
	@KIT=$(KIT) DEPLOY_TRUST_EC2=$(DEPLOY_TRUST_EC2) KIT_FILES="$(if $(KIT),$(KIT_FILE),$(call old_kits_hint,$(REPO_ROOT)/trust/)$(sort $(wildcard $(REPO_ROOT)/trust/.env.*.$(ENV))))" bash scripts/register-trusts.sh

.PHONY: package-onprem-trust-kit
package-onprem-trust-kit: ## Tarball trust/.env.<KIT>.<env> + FL kit slice for an on-prem operator
	## Does NOT edit the kit file. The admin populates trust/.env.<KIT>.<env> first:
	##   1. UI → Add Trust → paste the 5 KEY=VALUE modal lines into it.
	##   2. `make sync-trust-kit KIT=<slot> HUB=prod` (from repo root) — fills Hub-shared.
	## This target then reads FL_BACKEND / FL_KIT_SLOT / etc. out of the populated
	## kit file, slices the operator's portion of the FL participant kit out of
	## S3 (net-1/services/<slot>/ for nvflare; certificates/ + one
	## supernode_credentials_<N> for flower), and tarballs the kit file + slice
	## into deploy/providers/AWS/build/trust-kits/flip-trust-kit-<slot>-<date>.tar.gz.
	## Hand the tarball to the operator over an encrypted channel.
	## Required: KIT=<slot> (e.g. KIT=Trust_2).
	@[ -n "$(KIT)" ] || (echo "❌ KIT=<slot> is required (e.g. KIT=Trust_2)"; exit 1)
	@KIT=$(KIT) KIT_FILE=$(KIT_FILE) bash scripts/package-onprem-trust-kit.sh

.PHONY: delete-trust
delete-trust: ## Hard-delete a trust on the hub (frees the name + FL kit slot; cascades through dependent tables)
	## Runs flip_api.scripts.delete_trust as a one-off ECS task. Removes the trust row
	## entirely, NULLs its fl_kit_slot.assigned_to_trust_id, and deletes rows from the
	## eight tables holding non-cascading trust.id FKs (trust_task, fl_metrics, fl_logs,
	## fl_job_trust, model_trust_intersect, project_trust_intersect, query_result,
	## xnat_project_status). Operator can then UI-add a new trust with the freed name.
	## Required: NAME=<trust-name> matching trust.name exactly.
	@[ -n "$(NAME)" ] || (echo "❌ NAME=<trust-name> is required"; exit 1)
	@NAME="$(NAME)" bash scripts/delete-trust.sh

.PHONY: full-deploy
full-deploy: ## Full AWS deployment: provision infrastructure, deploy cloud Trust + Central Hub
	## Step order:
	## 1. login + init: auth and Terraform init
	## 2. generate-internal-service-key: mint the fl-server→hub key (idempotent)
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, secrets)
	## 4. update-env: refresh .env with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure the SSM bastion and provision the Trust EC2
	##    (ansible-init stages with trust_num=1 — no kit file exists yet; deploy-trust re-stages the kit
	##    for the registered slot via stage-fl-kit once register-trusts has written it)
	## 6. deploy-centralhub: deploy Central Hub ECS services at the env branch tip (new task-def revisions) + publish the UI
	## 7. register-trusts: register every live trust/.env.*.<env> kit on the hub and write kit files
	## 8. deploy-trust + status: re-stage the FL kit for the registered slot, seed the trust data with the stack down, bring up cloud Trust container stack and verify
	## Prerequisite: create stub kit files first: make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." TRUST_REGION=London HUB=$(HUB)
full-deploy: github-login aws-login init generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts deploy-trust status

.PHONY: full-deploy-hub-only
full-deploy-hub-only: ## Hub-only deployment: no cloud Trust EC2 — every trust runs on-prem (honours HUB)
	## Same chain as full-deploy minus the cloud-trust steps (deploy-trust, seed-trust-data),
	## with DEPLOY_TRUST_EC2=false so Terraform provisions no Trust EC2 at all. Use when the
	## trusts must run on hosts the cloud instance can't match (e.g. they need a GPU — the
	## trust EC2 is a t3.xlarge with none).
	## ssh-config skips the flip-trust alias and every `hosts: trust_ec2` Ansible play is a no-op.
	## NOTE: this only skips *creating* the Trust EC2. On an environment previously deployed
	## with full-deploy, apply will DESTROY the existing Trust EC2 (its data volumes are not
	## preserved) — re-run with DEPLOY_TRUST_EC2=true to bring one back.
	## After it completes, join each on-prem trust:
	##   1. make new-trust TRUST_CODE=<CODE> ... HUB=$(HUB) && make register-trusts KIT=<CODE> HUB=$(HUB)
	##      (register-trusts in this chain already registers every kit present)
	##   2. on the trust host: sudo -E make -C trust up-trust KIT=<CODE> HUB=<env>
	##      (sudo: the provisioned login user is deliberately not in the docker group)
	## Remember `make deploy-ui HUB=$(HUB)` — the UI ships separately, same as full-deploy.
full-deploy-hub-only: DEPLOY_TRUST_EC2 := false
full-deploy-hub-only: github-login aws-login init generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts status
