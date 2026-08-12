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

ifeq ($(PROD),true)
MAIN_ENV_FILE=../../../.env.production
else
# Sometimes PROD may not be set, default to staging environment in that case
PROD=stag
MAIN_ENV_FILE=../../../.env.stag
endif

ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

# Derive backend-specific DOCKER_FL_{API,SERVER,CLIENT}_NAME and FL_PROVISIONED_DIR
# from FL_BACKEND. The root Makefile and trust/Makefile already include this
# after their .env* block; running `make plan/apply` directly from this directory
# would otherwise leave the DOCKER_FL_* names empty and produce invalid ECS image
# references like `ghcr.io/londonaicentre/:stag`.
include ../../fl_backend.mk

LOCK ?= true
PRESERVE_VPC ?= true

# Export PROD so child processes (e.g. check_status.py) can determine the environment
export PROD

# Terraform and AWS CLI prefixes: unset static credential env vars that
# can leak from the parent shell (.env.stag may carry empty/literal-quoted
# AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY for the omop-db init job).
# These env vars take priority over the AWS_PROFILE / SSO credential chain
# in the AWS Go SDK (Terraform) and cause InvalidClientTokenId errors.
_TF := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; terraform
_AWS_ENV := unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; AWS_PROFILE=$(AWS_PROFILE)

# Resolve FL_KIT_DATE from the backend-specific kit date: FLARE_KIT_DATE or FLOWER_KIT_DATE
FL_KIT_DATE := $(if $(filter nvflare,$(FL_BACKEND)),$(FLARE_KIT_DATE),$(FLOWER_KIT_DATE))
$(if $(FL_KIT_DATE),,$(error 🧠 $(if $(filter nvflare,$(FL_BACKEND)),FLARE_KIT_DATE,FLOWER_KIT_DATE) must be set in the environment file for FL_BACKEND=$(FL_BACKEND)))

# Logical AWS profile names. Defaults match the short-alias pattern recommended
# in deploy/providers/AWS/README.md (operators add `[profile prod]` / `[profile stag]`
# blocks to their ~/.aws/config that point at the FLIP SSO accounts). Override via
# the env file or on the command line if your local profile names differ.
PROD_AWS_PROFILE ?= prod
STAG_AWS_PROFILE ?= stag

# Refuse to operate against an unintended AWS account. Profile name is the safety
# guard because Make has no easy way to read the account ID without an `aws` call.
ifeq ($(PROD),true)
ifneq ($(AWS_PROFILE),$(PROD_AWS_PROFILE))
$(error "For production deployments, AWS_PROFILE must be set to '$(PROD_AWS_PROFILE)' (override via PROD_AWS_PROFILE)")
endif
else
ifneq ($(AWS_PROFILE),$(STAG_AWS_PROFILE))
$(error "For staging deployments, AWS_PROFILE must be set to '$(STAG_AWS_PROFILE)' (override via STAG_AWS_PROFILE)")
endif
endif

## SETTING VARIABLES FOR TERRAFORM
# Map the Make-level PROD flag (true|stag) onto a Terraform-friendly environment name.
# Used in main.tf to gate prod-only RDS hardening (deletion protection, final snapshot).
export TF_VAR_environment=$(if $(filter true,$(PROD)),prod,stag)

# Kit-file env suffix: PROD=true → .production kits, otherwise → .stag. Selects
# the trust kit file trust/.env.<KIT>.<suffix> for the trust-deploy targets.
KIT_ENV_SUFFIX := $(if $(filter true,$(PROD)),production,stag)
export TF_VAR_max_azs=2
export TF_VAR_vpc_cidr=10.0.0.0/16
export TF_VAR_public_subnets=["10.0.1.0/24", "10.0.2.0/24"]
export TF_VAR_private_subnets=["10.0.101.0/24", "10.0.102.0/24"]
export TF_VAR_flip_keypair=~/.ssh/host-aws
export TF_VAR_ec2_public_key_path=$(HOME)/.ssh/host-aws.pub
export TF_VAR_flip_user_pool_name=flip-user-pool
export TF_VAR_flip_cognito_client=flip-cognito-client
export TF_VAR_FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME=${FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME}
export TF_VAR_FLIP_FL_RESULTS_BUCKET_NAME=${FLIP_FL_RESULTS_BUCKET_NAME}
export TF_VAR_FLIP_APP_BUNDLES_BUCKET_NAME=${FLIP_APP_BUNDLES_BUCKET_NAME}
export TF_VAR_AICENTRE_BUCKET_NAME=${AICENTRE_BUCKET_NAME}
# Fail fast with a useful message if the per-tenant bucket names are missing
# from the env file. The variables are required (no default) so Terraform
# would otherwise prompt mid-plan with a generic message that gives no hint
# about where to set the value.
$(if $(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME),,$(error 🪶 FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME must be set in $(MAIN_ENV_FILE)))
$(if $(FLIP_FL_RESULTS_BUCKET_NAME),,$(error 🪶 FLIP_FL_RESULTS_BUCKET_NAME must be set in $(MAIN_ENV_FILE)))
$(if $(FLIP_APP_BUNDLES_BUCKET_NAME),,$(error 🪶 FLIP_APP_BUNDLES_BUCKET_NAME must be set in $(MAIN_ENV_FILE)))
export TF_VAR_flip_cognito_admin_email=aicentreflip@gmail.com
export TF_VAR_flip_cognito_researcher_email=rafaelagd@gmail.com
export TF_VAR_POSTGRES_DB=${POSTGRES_DB}
export TF_VAR_AES_KEY_BASE64=${AES_KEY_BASE64}
export TF_VAR_INTERNAL_SERVICE_KEY_HASH=${INTERNAL_SERVICE_KEY_HASH}
export TF_VAR_INTERNAL_SERVICE_KEY=${INTERNAL_SERVICE_KEY}
export TF_VAR_INTERNAL_SERVICE_KEY_HEADER=${INTERNAL_SERVICE_KEY_HEADER}
# Fail fast with a useful message when these are missing — the variable is
# `sensitive` with no default, so otherwise Terraform errors out mid-plan
# with a generic "no value for required variable" that gives no hint about
# where the value should come from.
$(if $(INTERNAL_SERVICE_KEY),,$(error 🔑 INTERNAL_SERVICE_KEY must be set in $(MAIN_ENV_FILE) — generate with `make -C ../../../flip-api generate-internal-service-key`))
$(if $(INTERNAL_SERVICE_KEY_HASH),,$(error 🔑 INTERNAL_SERVICE_KEY_HASH must be set in $(MAIN_ENV_FILE) — must be sha256 of INTERNAL_SERVICE_KEY))
$(if $(INTERNAL_SERVICE_KEY_HEADER),,$(error 📨 INTERNAL_SERVICE_KEY_HEADER must be set in $(MAIN_ENV_FILE)))
export TF_VAR_MIN_CLIENTS=${MIN_CLIENTS}
# Per-job GPU resource spec forwarded to fl-api on ECS (mirrors compose). Default
# 0 in variables.tf; set JOB_RESOURCE_SPEC_NUM_GPUS/…_MEM_PER_GPU_IN_GIB in the
# env file for GPU jobs (e.g. Ark+ eval: NUM_GPUS=1, MEM_PER_GPU_IN_GIB=7).
# Export only when set: an unconditional export turns a missing env key into
# TF_VAR_…="" and Terraform rejects the empty string for a number variable
# instead of falling back to the variables.tf default.
ifneq ($(JOB_RESOURCE_SPEC_NUM_GPUS),)
export TF_VAR_JOB_RESOURCE_SPEC_NUM_GPUS=${JOB_RESOURCE_SPEC_NUM_GPUS}
endif
ifneq ($(JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB),)
export TF_VAR_JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB=${JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB}
endif
# Uncomment to enable ECS Exec on Fargate tasks for debugging:
# export TF_VAR_ecs_exec_enabled=true

export TF_VAR_docker_image_tag=${DOCKER_TAG}
export TF_VAR_flip_fl_image_tag=${DOCKER_FL_TAG}
export TF_VAR_docker_registry=${DOCKER_REGISTRY}
export TF_VAR_fl_api_name=${DOCKER_FL_API_NAME}
export TF_VAR_fl_server_name=${DOCKER_FL_SERVER_NAME}
export TF_VAR_fl_client_name=${DOCKER_FL_CLIENT_NAME}
export TF_VAR_fl_backend=${FL_BACKEND}
export TF_VAR_flare_kit_date=${FLARE_KIT_DATE}
export TF_VAR_flower_kit_date=${FLOWER_KIT_DATE}
export TF_VAR_POSTGRES_USER=${POSTGRES_USER}
export TF_VAR_DB_PORT=${DB_PORT}
export TF_VAR_UI_PORT=${UI_PORT}
export TF_VAR_API_PORT=${API_PORT}
export TF_VAR_FL_API_PORT=${FL_API_PORT}
export TF_VAR_FL_SERVER_PORT=${FL_SERVER_PORT}
# On-prem trust IPs allowed through the FL-server NLB security group. An HCL list
# literal in the env file (LOCAL_TRUST_PUBLIC_IPS, default []); one ingress rule
# is created per IP. `make allow-local-trust-nlb` applies them.
LOCAL_TRUST_PUBLIC_IPS ?= []
export TF_VAR_local_trust_public_ips=$(LOCAL_TRUST_PUBLIC_IPS)
# K8s trust node egress IPs — one FL-server NLB ingress rule per IP (#596).
# `make add-k8s-trust` applies them via a normal plan/apply (no -target).
K8S_TRUST_PUBLIC_IPS ?= []
export TF_VAR_k8s_trust_public_ips=$(K8S_TRUST_PUBLIC_IPS)
# Whether Terraform provisions the cloud Trust EC2. false = hub-only deployment
# (every trust runs on-prem — e.g. the workloads need a GPU the t3 trust host
# doesn't have). Settable in the env file or CLI; `make full-deploy-hub-only`
# forces it for its whole chain.
DEPLOY_TRUST_EC2 ?= true
export TF_VAR_deploy_trust_ec2=$(DEPLOY_TRUST_EC2)
export TF_VAR_AWS_REGION=${AWS_REGION}
export TF_VAR_VPC_NAME=${VPC_NAME}
export TF_VAR_ADMIN_USER_PASSWORD=${ADMIN_USER_PASSWORD}
export TF_VAR_SES_VERIFIED_EMAIL=${SES_VERIFIED_EMAIL}
export TF_VAR_flip_alb_subdomain=${ALB_SUBDOMAIN}
export TF_VAR_flip_nlb_subdomain=${NLB_SUBDOMAIN}
export TF_VAR_FLIP_UI_BUCKET_NAME=${FLIP_UI_BUCKET_NAME}
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
DEPLOY_GIT_REF := $(if $(filter true,$(PROD)),origin/main,origin/develop)

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

.PHONY: create-backend
create-backend: ## Create Terraform backend S3 bucket - Only run once
	@echo "🚀 Creating Terraform backend resources..."
	@TFSTATE_BUCKET_NAME="$(FLIP_TFSTATE_BUCKET_NAME)" TFSTATE_KEY="flip/terraform.tfstate" TFSTATE_REGION="$(AWS_REGION)" ./scripts/create-backend.sh

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


.PHONY: import-persistent
import-persistent: ## Import persistent resources (S3, Secrets, Cognito) to prevent replacement
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; bash scripts/import-resources.sh


.PHONY: migrate-flip-bucket
migrate-flip-bucket: ## One-time: sync legacy flip_bucket prefixes into the three new buckets (run after `make apply` creates the new buckets)
	## Source bucket: $${FLIP_BUCKET_NAME} (read from $(MAIN_ENV_FILE))
	## Destination buckets: $${FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME}, $${FLIP_FL_RESULTS_BUCKET_NAME}, $${FLIP_APP_BUNDLES_BUCKET_NAME}
	##
	## Sequencing: (1) `make apply` provisions the new buckets, (2) this target copies
	## prefixes into them, (3) `make verify-flip-bucket-migration` parity-checks, (4) redeploy
	## with the new env vars, (5) smoke-test, (6) after a 24-48h cooldown manually decommission
	## the old bucket with:
	##     aws s3 rb s3://$${FLIP_BUCKET_NAME} --force    # only after `make verify-flip-bucket-migration` is green
	## Note: `terraform state rm` is NOT needed — the `removed { destroy = false }`
	## blocks in services.tf already forgot the legacy bucket on the last apply.
	##
	## Safe to re-run: `aws s3 sync` re-copies any source object whose mtime/size differs
	## from the destination, and skips matching ones. Per-object failures (KMS grant gaps,
	## throttling) do NOT cause `aws s3 sync` to exit non-zero — always finish with
	## `make verify-flip-bucket-migration` before trusting that the migration completed.
	@if [ -z "$(FLIP_BUCKET_NAME)" ]; then \
		echo "❌ FLIP_BUCKET_NAME is not set in $(MAIN_ENV_FILE) — nothing to migrate from."; exit 1; \
	fi
	@echo "📦 Migrating contents of s3://$(FLIP_BUCKET_NAME) into the three split buckets ..."
	@echo "  • model file uploads → s3://$(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)/uploaded/"
	@$(_AWS_ENV) aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/model_files/uploaded/ \
		s3://$(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)/uploaded/
	@echo "  • FL results     → s3://$(FLIP_FL_RESULTS_BUCKET_NAME)/"
	@$(_AWS_ENV) aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/uploaded_federated_data/ \
		s3://$(FLIP_FL_RESULTS_BUCKET_NAME)/
	# The legacy base-application/ prefix is intentionally NOT migrated: the base FL
	# application templates now ship in the flip-api image, not S3 (FLIP#724).
	@echo "  • FL app dests   → s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/app_destinations/"
	@$(_AWS_ENV) aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/app_destination_bucket/ \
		s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/app_destinations/
	@echo "📦 Sync calls complete. Run \`make verify-flip-bucket-migration\` to confirm parity before decommissioning."


.PHONY: verify-flip-bucket-migration
verify-flip-bucket-migration: ## Parity-check the three migrated prefixes (every src key must exist on dst; extras on dst are fine)
	## Why this exists: `aws s3 sync` returns exit code 0 even when individual object copies
	## fail (per-object AccessDenied / throttling / RequestTimeout), so the migrate target's
	## green banner is not enough to safely decommission the source bucket. The companion
	## script computes a key-set subset check — every source key must exist on the
	## destination. Keys present only on destination (e.g. researcher uploads landing on the
	## new bucket after the env-var cutover) are reported but do not fail.
	@bash scripts/verify-flip-bucket-migration.sh


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
	## `make apply` for this (AMI drift can force-replace trust EC2s).
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
add-fl-kits: ## Ensure N more claimable FL kit slots exist, end to end (NVFLARE; PROD=stag|true)
	## Wraps scripts/add_fl_kits.sh: discover nets from S3 → ACTIVATE up to N spares
	## (kits already minted on every net but not yet in FL_KIT_SLOT_NAMES — no certs, no
	## upload) → MINT only the shortfall on every net (restore/fingerprint-verify each
	## net's CA workspace, additive S3 upload, never sync --delete) → append the activated
	## + minted names to FL_KIT_SLOT_NAMES in the env file. Then re-invokes make for
	## apply-fl-kit-slots so the sub-make re-reads the just-edited env file (this parse's
	## TF_VAR_FL_KIT_SLOT_NAMES predates the edit). N is a target ("N more live slots"),
	## not a mint count — spares are reused before any fresh mint, so a good kit is never
	## stranded and, when spares cover N, nothing is minted at all.
	@[ -n "$(N)" ] || { echo "❌ N is required, e.g. make add-fl-kits N=2 PROD=true"; exit 1; }
	@$(_AWS_ENV) MAIN_ENV_FILE=$(MAIN_ENV_FILE) N=$(N) YES=$(YES) \
		FL_BACKEND=$(FL_BACKEND) ./scripts/add_fl_kits.sh
	@$(MAKE) apply-fl-kit-slots PROD=$(PROD) YES=$(YES) || { \
	  echo "❌ Parameter apply failed AFTER the new slot names were appended to the env file"; \
	  echo "   (and any shortfall minted + uploaded)."; \
	  echo "   Recover with just: make apply-fl-kit-slots PROD=$(PROD)"; \
	  echo "   Do NOT re-run add-fl-kits — it would grow the pool by another N slots (activating"; \
	  echo "   more spares or minting higher-numbered kits)."; \
	  exit 1; }

.PHONY: destroy-cert
destroy-cert: ## Destroy only the ACM certificate (use with caution - will break HTTPS)
	@echo "💥 Destroying ACM certificate (Step 1/2)..."
	@$(_TF) destroy -target=aws_acm_certificate.flip -target=aws_acm_certificate_validation.flip -target=aws_route53_record.cert_validation
	@echo "✅ Certificate destroyed! Now run 'make plan' and 'make apply' to remove DNS validation."

.PHONY: plan-cloudfront-certs
plan-cloudfront-certs: ## Plan the CloudFront viewer cert (us-east-1)
	@echo "📋 Planning CloudFront viewer certificate..."
	@$(_TF) plan \
		-target=aws_acm_certificate.flip_cloudfront \
		-out=plan-cloudfront-certs.tfplan

.PHONY: apply-cloudfront-certs
apply-cloudfront-certs: ## Apply the CloudFront viewer cert (us-east-1)
	@echo "🔒 Applying CloudFront viewer certificate (Step 1/2 for CloudFront)..."
	@$(_TF) apply -target=aws_acm_certificate.flip_cloudfront -auto-approve
	@echo "✅ Certificate created. Run 'make plan' and 'make apply' to deploy DNS validation + CloudFront."

.PHONY: refresh
refresh: ## Refresh Terraform state to sync with actual AWS resources
	@echo "🔄 Refreshing Terraform state..."
	@$(_TF) refresh
	@echo "✅ State refreshed!"


.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure (with environment file loaded)
	@echo "🛠️ Applying Terraform configuration..."
	@$(_TF) apply plan.tfplan

.PHONY: import-cloudfront
import-cloudfront: ## Import existing CloudFront distribution into terraform state
	@echo "🔗 Importing existing CloudFront distribution..."
	@$(_TF) import aws_cloudfront_distribution.flip_ui E31HHLGOWBDENB

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
	@[ -n "$(OMOP_DATA_VERSION)" ] || (echo "❌  trust/omop-db/.data_version not found"; exit 1)
	@[ -n "$(ORTHANC_DATA_VERSION)" ] || (echo "❌  trust/orthanc/.data_version not found"; exit 1)
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@# --skip-tags omop-vocab: the vocabulary load needs the trust's OMOP cluster
	@# credentials, which live in the kit file. This target runs BEFORE
	@# register-trusts in every deploy chain, so it has no KIT in scope (hence
	@# trust_num falling back to 1 above) and cannot supply them. The vocabulary
	@# is seeded later by `make seed-trust-data KIT=<CODE>`, which does have them.
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; uv run ansible-playbook site.yml \
		--skip-tags omop-vocab \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_backend=$(FL_BACKEND) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_num=$(or $(FL_KIT_SLOT_NUMBER),1) \
		-e omop_data_version=$(OMOP_DATA_VERSION) \
		-e orthanc_data_version=$(ORTHANC_DATA_VERSION)

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
		-e omop_data_version=$(OMOP_DATA_VERSION) \
		-e orthanc_data_version=$(ORTHANC_DATA_VERSION)
	@echo "✅ Central Hub SSM bastion provisioned."

# Cloud-trust targets are meaningless on a hub-only deployment — fail fast with
# a pointer to the on-prem flow instead of an obscure SSH/inventory error.
.PHONY: check-trust-ec2-enabled
check-trust-ec2-enabled:
	@[ "$(DEPLOY_TRUST_EC2)" != "false" ] || (echo "❌  DEPLOY_TRUST_EC2=false (hub-only deployment) — there is no cloud Trust EC2. On-prem trusts join via register-trusts + allow-local-trust-nlb and run 'sudo -E make -C trust up-trust' on their own host."; exit 1)

.PHONY: seed-trust-data
seed-trust-data: check-trust-ec2-enabled ssh-config ## Load trust-specific OMOP + Orthanc mock data onto the Trust EC2 (re-run after data version bump; requires KIT=)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make seed-trust-data KIT=Trust_2 PROD=stag"; exit 1)
	@[ -n "$(FL_KIT_SLOT_NUMBER)" ] || (echo "❌  FL_KIT_SLOT_NUMBER not found in trust/.env.$(KIT).$(KIT_ENV_SUFFIX) — run register-trusts first"; exit 1)
	@[ -n "$(OMOP_DATA_VERSION)" ] || (echo "❌  trust/omop-db/.data_version not found"; exit 1)
	@[ -n "$(ORTHANC_DATA_VERSION)" ] || (echo "❌  trust/orthanc/.data_version not found"; exit 1)
	@[ -n "$(OMOP_POSTGRES_PASSWORD_KIT)" ] || (echo "❌  OMOP_POSTGRES_PASSWORD not found in trust/.env.$(KIT).$(KIT_ENV_SUFFIX) — the vocab-load play needs the cluster credentials"; exit 1)
	@echo "🗄️  Seeding Trust EC2 with trust$(FL_KIT_SLOT_NUMBER) data (OMOP v$(OMOP_DATA_VERSION), Orthanc v$(ORTHANC_DATA_VERSION))..."
	@uv run ansible-playbook site.yml \
		--tags data \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e trust_num=$(FL_KIT_SLOT_NUMBER) \
		-e omop_data_version=$(OMOP_DATA_VERSION) \
		-e orthanc_data_version=$(ORTHANC_DATA_VERSION) \
		-e omop_postgres_user=$(OMOP_POSTGRES_USER_KIT) \
		-e omop_postgres_password=$(OMOP_POSTGRES_PASSWORD_KIT) \
		-e omop_postgres_db=$(OMOP_POSTGRES_DB_KIT) \
		-e vocab_s3_bucket=$(AICENTRE_BUCKET_NAME) \
		-e omop_db_tag=$(DOCKER_TAG)
	@echo "✅ Trust data seeding complete."

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
	## never by index — matching the describe-tasks convention in TROUBLESHOOTING.md §1.9
	## and staying robust to sidecar/ordering changes; only its image tag is swapped before
	## registering the new revision. Task-def tags are carried over only when non-empty
	## (register-task-definition rejects an empty tags array). Pass TAG=sha-<short7> to
	## deploy a hotfix / feature-branch image (build it first via workflow_dispatch — see
	## CLAUDE.md); revert with `make rollback-centralhub`.
	## FL quiesce warning (FLIP#770): an ECS replacement kills any in-flight training run
	## (fl-server run state is ephemeral) and strands its model/net BUSY. For now this is a
	## printed reminder only, not an enforced gate — enable Deployment Mode first (it pauses
	## FL job pickup) and wait for any running job to finish; GET /fl/quiesce reports both.
	## On prod (PROD=true) the reminder also asks for interactive confirmation; stag proceeds
	## without prompting so scripted staging deploys stay non-interactive.
	@set -e; \
	export AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION); \
	echo "⚠️  FL quiesce reminder (FLIP#770): replacing fl-server-net-1 kills any in-flight"; \
	echo "   training run and strands its model at TRAINING_STARTED with its net stuck BUSY."; \
	echo "   Before deploying: enable Deployment Mode (Admin → Deployments) — it pauses FL job"; \
	echo "   pickup, so queued jobs hold — and wait for any running job to finish."; \
	echo "   GET $(CENTRAL_HUB_API_URL)/fl/quiesce reports both (deployment_mode + fl_quiesced)."; \
	if [ "$(PROD)" = "true" ]; then \
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
	printf '%s' "$$TAG" | grep -Eq '^sha-[0-9a-f]{7}$$' || { \
	  echo "❌ TAG '$$TAG' is not an immutable sha-<short7> tag (sha- followed by 7 hex chars)."; \
	  echo "   deploy-centralhub only deploys CI-published sha tags — a mutable tag here would"; \
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
	  case "$$image" in \
	  *@*) echo "❌ $$service: digest-pinned image '$$image' — cannot swap a tag onto it"; exit 1;; \
	  esac; \
	  new_image="$${image%:*}:$$TAG"; \
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
	  new_image="$${image%:*}:$$TAG"; \
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
	  echo "   Revert with: make rollback-centralhub PROD=$(PROD)"; \
	  exit 1; }
	@$(MAKE) deploy-ui PROD=$(PROD)
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
	@set -e; \
	unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; export AWS_PROFILE=$(AWS_PROFILE) AWS_REGION=$(AWS_REGION); \
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
	@echo "   UI, re-run 'make deploy-ui PROD=$(PROD)' from the matching commit to avoid a"
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
		--exclude "index.html" --exclude "js/window.js"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/index.html s3://$$UI_BUCKET/index.html \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "text/html; charset=utf-8"; \
	$(_AWS_ENV) aws s3 cp ../../../flip-ui/dist/js/window.js s3://$$UI_BUCKET/js/window.js \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "application/javascript; charset=utf-8"
	@echo "🧹 Invalidating CloudFront..."
	@DIST_ID=$$($(_AWS_ENV) terraform output -raw CloudfrontDistributionId); \
	[ -n "$$DIST_ID" ] || { echo "❌ Terraform output CloudfrontDistributionId is empty"; exit 1; }; \
	$(_AWS_ENV) aws cloudfront create-invalidation \
		--distribution-id "$$DIST_ID" \
		--paths "/*" >/dev/null
	@echo "✅ deploy-ui complete."

# Identity of the trust kit being deployed/provisioned — a trust CODE that
# selects its kit file trust/.env.<KIT>.<env> and its trust-local data dirs.
# REQUIRED on the command line for trust targets (no default, so a prod op
# can't silently target the wrong trust). Scaffold one with `make new-trust`.
# FL participant kit slot the trust's fl-client mounts — names the
# services/<slot>/ dir in the S3 participant-kit bundle and under /opt/flip.
# Read from the trust's kit file (the single source of truth: the kit file's
# FL_KIT_SLOT is also what trust/Makefile mounts), not duplicated here.
FL_KIT_SLOT := $(shell sed -n 's/^FL_KIT_SLOT=//p' ../../../trust/.env.$(KIT).$(KIT_ENV_SUFFIX) 2>/dev/null)
FL_KIT_SLOT_NUMBER := $(shell sed -n 's/^FL_KIT_SLOT_NUMBER=//p' ../../../trust/.env.$(KIT).$(KIT_ENV_SUFFIX) 2>/dev/null)

# Trust-local OMOP DB credentials from the kit — passed to the vocab-load play
# (they must match the values the restored pgdata cluster was built with).
OMOP_POSTGRES_USER_KIT := $(shell sed -n 's/^OMOP_POSTGRES_USER=//p' ../../../trust/.env.$(KIT).$(KIT_ENV_SUFFIX) 2>/dev/null)
OMOP_POSTGRES_PASSWORD_KIT := $(shell sed -n 's/^OMOP_POSTGRES_PASSWORD=//p' ../../../trust/.env.$(KIT).$(KIT_ENV_SUFFIX) 2>/dev/null)
OMOP_POSTGRES_DB_KIT := $(shell sed -n 's/^OMOP_POSTGRES_DB=//p' ../../../trust/.env.$(KIT).$(KIT_ENV_SUFFIX) 2>/dev/null)

# Data versions read from the canonical .data_version files — these govern both
# the local update-omop-data / update-orthanc-data scripts and the Ansible plays.
OMOP_DATA_VERSION := $(shell cat ../../../trust/omop-db/.data_version 2>/dev/null | tr -d ' \n\r\t')
ORTHANC_DATA_VERSION := $(shell cat ../../../trust/orthanc/.data_version 2>/dev/null | tr -d ' \n\r\t')

.PHONY: deploy-trust
deploy-trust: check-trust-ec2-enabled ssh-config update-env seed-trust-data ## Deploy trust relationships after central hub is deployed
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
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../trust down-trust-ec2 KIT=$(KIT) PROD=${PROD} 2>/dev/null || true
	@docker --context flip-trust image prune -af 2>/dev/null || true
	@echo "🚀 Deploying Trust services..."
	DOCKER_CONTEXT=flip-trust $(MAKE) -C ../../../ up-trust-ec2 KIT=$(KIT) PROD=${PROD}
	@echo "✅ Trust deployment complete!"

.PHONY: provision-local-trust
provision-local-trust: ## Provision the on-prem (local) trust host: install Docker + deps, stage the FL kit
	## Run this ON the trust host. Configures the machine via Ansible (local connection) and
	## downloads the FL participant kit from S3. Requires ANSIBLE_BECOME_PASS in the environment.
	## The S3 kit download needs AWS credentials (fine for FLIP-team testing); for real on-prem
	## trusts the participant kit is delivered to the operator out-of-band.
	@echo "🔧 Provisioning the on-premises Trust host (this machine)..."
	@uv sync
	@uv run ansible-galaxy install -r ../../../deploy/providers/local/requirements.yml
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
			../../../deploy/providers/local/site_local_trust.yml; \
		PLAY_RC=$$?; \
		exit $$PLAY_RC
	@echo "📦 Downloading the FL participant kit for the on-prem trust from S3..."
ifeq ($(FL_BACKEND),nvflare)
	@[ -n "$(FL_KIT_SLOT)" ] || (echo ""; echo "❌  FL_KIT_SLOT not found — is trust/.env.$(KIT).$(KIT_ENV_SUFFIX) present with FL_KIT_SLOT set?"; echo ""; exit 1)
	@TRUST_KIT_DIR=/tmp/$(FL_KIT_SLOT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR && \
	  $(_AWS_ENV) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FL_KIT_DATE)/net-1/services/$(FL_KIT_SLOT)/ \
	    $$TRUST_KIT_DIR && \
	  echo "" && \
	  echo "  # Deploy the $(FL_KIT_SLOT) NVFLARE participant kit (staged at $$TRUST_KIT_DIR):" && \
	  echo "  sudo mkdir -p /opt/flip/services/$(FL_KIT_SLOT)/startup /opt/flip/services/$(FL_KIT_SLOT)/local /opt/flip/services/$(FL_KIT_SLOT)/transfer" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/startup/ /opt/flip/services/$(FL_KIT_SLOT)/startup/" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/local/ /opt/flip/services/$(FL_KIT_SLOT)/local/" && \
	  echo "  # fl-client now runs as a non-root uid (GHSA-8465) — hand the rsync'd kit back to" && \
	  echo "  # this shell's own user so the container can read/write it:" && \
	  echo "  sudo chown -R \$$(id -u):\$$(id -g) /opt/flip/services/$(FL_KIT_SLOT)" && \
	  echo "✅ $(FL_KIT_SLOT) NVFLARE kit staged"
else
	@TRUST_KIT_DIR=/tmp/$(KIT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR/net-1 && \
	  $(_AWS_ENV) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flower-participant-kits/$(FL_KIT_DATE)/net-1/ \
	    $$TRUST_KIT_DIR/net-1/ && \
	  echo "" && \
	  echo "  # Deploy the Flower participant kit (staged at $$TRUST_KIT_DIR):" && \
	  echo "  sudo mkdir -p /opt/flip/services/net-1" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/net-1/ /opt/flip/services/net-1/" && \
	  echo "✅ Flower kit staged"
endif
	@echo ""
	@echo "✅ On-premises Trust host provisioned!"
	@echo ""
	@echo "⚠️  REMAINING MANUAL STEPS:"
	@echo "   1. Deploy the staged FL kit into /opt/flip (run the commands printed above)."
	@echo "   2. Start the trust stack (sudo: the login user is deliberately not in the docker group):"
	@echo "      cd ../../.. && sudo -E env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)"
	@echo "   3. Report this host's public IP to the FLIP admin — they run:"
	@echo "      make allow-local-trust-nlb LOCAL_TRUST_IP=<this-host-public-ip>"

.PHONY: allow-local-trust-nlb
allow-local-trust-nlb: ## Apply the FL-server NLB ingress rules for the IPs in LOCAL_TRUST_PUBLIC_IPS
	## Run by the FLIP admin. First add each on-prem trust's public IP to LOCAL_TRUST_PUBLIC_IPS
	## in the env file (an HCL list, e.g. LOCAL_TRUST_PUBLIC_IPS=["1.2.3.4"]). Pass LOCAL_TRUST_IP=<ip>
	## to have it checked against the configured list first. This is a normal full terraform
	## plan/apply — the rules are honest config, so there is no -target and no drift on later applies.
	@if [ -n "$(LOCAL_TRUST_IP)" ] && ! echo '$(LOCAL_TRUST_PUBLIC_IPS)' | grep -q '"$(LOCAL_TRUST_IP)"'; then \
		echo ""; \
		echo "❌  $(LOCAL_TRUST_IP) is not in LOCAL_TRUST_PUBLIC_IPS (currently $(LOCAL_TRUST_PUBLIC_IPS))."; \
		echo "   Add it to LOCAL_TRUST_PUBLIC_IPS in $(MAIN_ENV_FILE), then re-run, e.g.:"; \
		echo "       LOCAL_TRUST_PUBLIC_IPS=[\"$(LOCAL_TRUST_IP)\"]"; \
		echo ""; \
		exit 1; \
	fi
	@echo "🔐 Applying FL-server NLB ingress rules for LOCAL_TRUST_PUBLIC_IPS=$(LOCAL_TRUST_PUBLIC_IPS)..."
	@$(MAKE) plan
	@$(MAKE) apply
	@echo "✅ FL-server NLB security group reconciled."

.PHONY: add-k8s-trust
add-k8s-trust: ## Open the FL-server NLB to a K8s trust node's public IP (FL training only)
	## A K8s trust is registered exactly like any other trust — by CODE-named kit:
	##   make new-trust TRUST_CODE=Trust_K8s TRUST_NAME="..."
	##   make -C deploy/providers/AWS register-trusts KIT=Trust_K8s PROD=$(PROD)
	##   make sync-trust-kit KIT=Trust_K8s PROD=$(PROD)
	##   make -C deploy/providers/kubernetes sync-kit KIT=Trust_K8s PROD=$(PROD)
	##   make -C deploy/providers/kubernetes up OVERRIDES_FILE=k8s-trust-Trust_K8s.yaml
	## Polling needs none of the below. This target is the LAST step, needed only for
	## FL *training*: it opens the FL-server NLB security group to the K8s node's
	## public/egress IP. Mirrors `allow-local-trust-nlb`: add each node IP to
	## K8S_TRUST_PUBLIC_IPS in the env file (an HCL list, e.g.
	## K8S_TRUST_PUBLIC_IPS=["1.2.3.4"]), then this runs a normal full plan/apply.
	## The IPs are honest config (no -target), so the rule set is idempotent and
	## re-running with an already-listed IP is a no-op — fixing #596.
	@if [ -n "$(K8S_TRUST_IP)" ] && ! echo '$(K8S_TRUST_PUBLIC_IPS)' | grep -q '"$(K8S_TRUST_IP)"'; then \
		echo ""; \
		echo "❌  $(K8S_TRUST_IP) is not in K8S_TRUST_PUBLIC_IPS (currently $(K8S_TRUST_PUBLIC_IPS))."; \
		echo "   Add it to K8S_TRUST_PUBLIC_IPS in $(MAIN_ENV_FILE), then re-run, e.g.:"; \
		echo "       K8S_TRUST_PUBLIC_IPS=[\"$(K8S_TRUST_IP)\"]"; \
		echo ""; \
		exit 1; \
	fi
	@echo "🔐 Applying FL-server NLB ingress rules for K8S_TRUST_PUBLIC_IPS=$(K8S_TRUST_PUBLIC_IPS)..."
	@$(MAKE) plan
	@$(MAKE) apply
	@echo "✅ FL-server NLB security group reconciled for the K8s trust node(s)."

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
destroy: ## Destroy ephemeral infrastructure (EC2, RDS, optional VPC) - preserves Cognito, Secrets, S3
	@echo ""
	@echo "⚠️  SELECTIVE DESTRUCTION - PRESERVING:"
	@echo "   ✓ Cognito user pool and identities"
	@echo "   ✓ AWS Secrets Manager (FLIP_API secret)"
	@echo "   ✓ S3 buckets (FLIP + AI Centre data)"
	@echo "   ✓ ACM certificates and Route53 records"
ifneq ($(filter true,$(PROD) $(PRESERVE_VPC)),)
	@echo "   ✓ VPC (preserved)"
endif
	@echo ""
	@echo "🗑️  DESTROYING:"
	@echo "   • EC2 instances (flip, trust)"
	@echo "   • RDS database (PostgreSQL)"
	@echo "   • Security groups"
ifeq ($(filter true,$(PROD) $(PRESERVE_VPC)),)
	@echo "   • VPC"
endif
	@echo "   • ALB/NLB load balancers"
	@echo "   • IAM roles and policies"
	@echo "   • CloudWatch logs"
	@echo ""
	@bash -c 'read -p "Type '\''yes'\'' to confirm destruction: " confirm && [ "$$confirm" = "yes" ] || { echo "Aborted."; exit 1; }'
	@echo ""
	@unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN; PRESERVE_VPC=$(PRESERVE_VPC) bash scripts/destroy-selective.sh


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
	##   (no KIT)   → register every live trust/.env.*.<env> kit
	@KIT=$(KIT) bash scripts/register-trusts.sh

.PHONY: package-onprem-trust-kit
package-onprem-trust-kit: ## Tarball trust/.env.<KIT> + FL kit slice for an on-prem operator
	## Does NOT edit the kit file. The admin populates trust/.env.<KIT> first:
	##   1. UI → Add Trust → paste the 5 KEY=VALUE modal lines into trust/.env.<KIT>.
	##   2. `make sync-trust-kit KIT=<slot> PROD=true` (from repo root) — fills Hub-shared.
	## This target then reads FL_BACKEND / FL_KIT_SLOT / etc. out of the populated
	## kit file, slices the operator's portion of the FL participant kit out of
	## S3 (net-1/services/<slot>/ for nvflare; certificates/ + one
	## supernode_credentials_<N> for flower), and tarballs the kit file + slice
	## into deploy/providers/AWS/build/trust-kits/flip-trust-kit-<slot>-<date>.tar.gz.
	## Hand the tarball to the operator over an encrypted channel.
	## Required: KIT=<slot> (e.g. KIT=Trust_2).
	@[ -n "$(KIT)" ] || (echo "❌ KIT=<slot> is required (e.g. KIT=Trust_2)"; exit 1)
	@KIT=$(KIT) bash scripts/package-onprem-trust-kit.sh

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
	## 1. login + init + import: auth, Terraform init, import any pre-existing resources
	## 2. generate-internal-service-key: mint the fl-server→hub key (idempotent)
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, secrets)
	## 4. update-env: refresh .env with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure the SSM bastion and provision the Trust EC2
	##    (ansible-init uses trust_num=1 by default; re-run with KIT=<CODE> after register-trusts for slot-specific data)
	## 6. deploy-centralhub: deploy Central Hub ECS services at the env branch tip (new task-def revisions) + publish the UI
	## 7. register-trusts: register every live trust/.env.*.<env> kit on the hub and write kit files
	## 8. deploy-trust + status: bring up cloud Trust container stack and verify
	## Prerequisite: create stub kit files first: make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." TRUST_REGION=London PROD=$(PROD)
full-deploy: github-login aws-login init import-persistent generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts deploy-trust status

.PHONY: full-deploy-hub-only
full-deploy-hub-only: ## Hub-only deployment: no cloud Trust EC2 — every trust runs on-prem (honours PROD)
	## Same chain as full-deploy minus the cloud-trust steps (deploy-trust, seed-trust-data),
	## with DEPLOY_TRUST_EC2=false so Terraform provisions no Trust EC2 at all. Use when the
	## trusts must run on hosts the cloud instance can't match (e.g. they need a GPU — the
	## trust EC2 is a t3.xlarge with none).
	## ssh-config skips the flip-trust alias and every `hosts: trust_ec2` Ansible play is a no-op.
	## NOTE: this only skips *creating* the Trust EC2. On an environment previously deployed
	## with full-deploy, apply will DESTROY the existing Trust EC2 (its data volumes are not
	## preserved) — re-run with DEPLOY_TRUST_EC2=true to bring one back.
	## After it completes, join each on-prem trust:
	##   1. add the trust host's public IP to LOCAL_TRUST_PUBLIC_IPS in the env file, then
	##      make allow-local-trust-nlb PROD=$(PROD)
	##   2. make new-trust TRUST_CODE=<CODE> ... PROD=<env> && make register-trusts KIT=<CODE> PROD=$(PROD)
	##      (register-trusts in this chain already registers every kit present)
	##   3. on the trust host: sudo -E env PROD=<env> make -C trust up-trust KIT=<CODE>
	##      (sudo: the provisioned login user is deliberately not in the docker group)
	## Remember `make deploy-ui PROD=$(PROD)` — the UI ships separately, same as full-deploy.
full-deploy-hub-only: DEPLOY_TRUST_EC2 := false
full-deploy-hub-only: github-login aws-login init import-persistent generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts status

.PHONY: full-deploy-with-local-trust
full-deploy-with-local-trust: ## Full AWS deployment (honours PROD) plus on-prem trust provisioning (requires LOCAL_TRUST_IP=<public-ip>)
	## Works against either PROD=true or PROD=stag — PROD is inherited from the environment.
	## Step order:
	## 1. login + init + import: auth, Terraform init, import pre-existing persistent resources
	## 2. generate-internal-service-key: mint the fl-server→hub key (idempotent)
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, ALB, secrets, on-prem trust NLB rule)
	## 4. update-env: refresh .env.{PROD} with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure the SSM bastion and provision the Trust EC2
	##    (ansible-init uses trust_num=1 by default; re-run with KIT=<CODE> after register-trusts for slot-specific data)
	## 6. deploy-centralhub: deploy Central Hub ECS services at the env branch tip (new task-def revisions) + publish the UI
	## 7. register-trusts: register every live trust/.env.*.<env> kit on the hub and write kit files
	## 8. deploy-trust + provision-local-trust + status: bring up cloud + on-prem trust hosts and verify
	## NOTE: After this completes you must manually:
	##   - Start the on-prem trust stack (sudo: the provisioned login user is deliberately not in
	##     the docker group): cd ../../.. && sudo -E env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make full-deploy-with-local-trust LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
# The one on-prem trust IP this all-in-one run opens the NLB to — the chain's `apply`
# step creates the SG rule. For multiple trusts use the async flow instead: set
# LOCAL_TRUST_PUBLIC_IPS in the env file and run `make allow-local-trust-nlb`.
full-deploy-with-local-trust: LOCAL_TRUST_PUBLIC_IPS := ["$(LOCAL_TRUST_IP)"]
full-deploy-with-local-trust: github-login aws-login init import-persistent generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts deploy-trust provision-local-trust status

.PHONY: full-deploy-hybrid
full-deploy-hybrid: ## Full hybrid deployment including on-prem trust provisioning + hub redeploy (honours PROD)
	## This wraps full-deploy-with-local-trust and then redeploys the Central Hub so the hub
	## picks up any env/secret changes made while registering the on-prem trust.
	## Works against either PROD=true or PROD=stag — PROD is inherited from the environment.
	## You still need to start the on-prem trust stack on the trust host.
	## If LOCAL_TRUST_IP is not provided, it will be auto-detected using curl (operator machine is the trust host).
	@if [ -z "$(LOCAL_TRUST_IP)" ]; then \
		echo "🔍 Detecting public IP for LOCAL_TRUST_IP..."; \
		LOCAL_TRUST_IP=$$(curl -s https://api.ipify.org) && \
		echo "✅ Detected LOCAL_TRUST_IP: $$LOCAL_TRUST_IP" || (echo "❌  Failed to detect IP. Provide LOCAL_TRUST_IP manually."; echo "   Usage: make full-deploy-hybrid LOCAL_TRUST_IP=<public-ip>"; exit 1); \
	else \
		LOCAL_TRUST_IP="$(LOCAL_TRUST_IP)"; \
	fi && \
	$(MAKE) full-deploy-with-local-trust LOCAL_TRUST_IP=$$LOCAL_TRUST_IP KIT="$(KIT)" && \
	$(MAKE) deploy-centralhub PROD=$(PROD) && \
	echo "" && \
	echo "✅ Hybrid deployment complete (PROD=$(PROD))." && \
	echo "➡️  Final manual step: start the on-prem trust stack on the trust host" && \
	echo "   (sudo: the provisioned login user is deliberately not in the docker group):" && \
	echo "   cd ../../.. && sudo -E env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)"

.PHONY: preflight-k8s-deploy
preflight-k8s-deploy: ## Pre-flight checks for full-deploy-with-k8s (Trust_K8s slot, kubectl context, helm)
	@uv run --no-config scripts/preflight_k8s_deploy.py \
		--prod "$(PROD)" \
		$(if $(K8S_TRUST_IP),--k8s-trust-ip "$(K8S_TRUST_IP)")

.PHONY: full-deploy-with-k8s
full-deploy-with-k8s: ## Full AWS deploy + Trust_K8s registration and K8s chart deployment
	## Step order:
	## 1. Detect or validate K8S_TRUST_IP (deployer machine's public egress IP)
	## 2. preflight-k8s-deploy: fail-fast if Trust_K8s not in FL_KIT_SLOT_NAMES, kubectl/helm missing
	## 3–9. full-deploy steps (provision AWS infra, central hub, EC2 trust + EC2 status gate)
	## 10. Scaffold trust/.env.Trust_K8s.<env> if absent (make new-trust, idempotent)
	## 11. Register Trust_K8s on the hub (register-trusts, idempotent)
	## 12. Sync hub-shared block into Trust_K8s kit (sync-trust-kit)
	## 13. Patch Trust_K8s secrets into the K8s cluster Secret (kubernetes sync-kit)
	## 14. Open FL-server NLB to K8S_TRUST_IP (add-k8s-trust, Terraform -target)
	## 15. Deploy K8s trust Helm chart (make -C kubernetes up)
	## 16. Status: K8s trust (make -C kubernetes status) — EC2 trust checked in step 3–9
	## Prerequisite: add "Trust_K8s" to FL_KIT_SLOT_NAMES in .env.stag before running
	@_K8S_IP="$(K8S_TRUST_IP)"; \
	if [ -z "$$_K8S_IP" ]; then \
		echo "🔍 Detecting K8S_TRUST_IP..."; \
		_K8S_IP=$$(curl -s --max-time 5 https://api.ipify.org) || { \
			echo "❌  Cannot auto-detect public IP."; \
			echo "   Provide it explicitly: make full-deploy-with-k8s K8S_TRUST_IP=<ip> PROD=$(PROD)"; \
			exit 1; \
		}; \
		echo "✅ K8S_TRUST_IP: $$_K8S_IP"; \
	fi && \
	$(MAKE) preflight-k8s-deploy PROD=$(PROD) K8S_TRUST_IP=$$_K8S_IP && \
	$(MAKE) full-deploy PROD=$(PROD) KIT=$(KIT) && \
	echo "" && \
	echo "☸️  Setting up Trust_K8s..." && \
	{ [ -f ../../../trust/.env.Trust_K8s.$(KIT_ENV_SUFFIX) ] || \
		$(MAKE) -C ../../.. new-trust \
			TRUST_CODE=Trust_K8s \
			TRUST_NAME="Kubernetes Trust" \
			TRUST_REGION=London \
			PROD=$(PROD); } && \
	$(MAKE) register-trusts KIT=Trust_K8s PROD=$(PROD) && \
	$(MAKE) -C ../../.. sync-trust-kit KIT=Trust_K8s PROD=$(PROD) && \
	$(MAKE) -C ../kubernetes sync-kit KIT=Trust_K8s PROD=$(PROD) && \
	$(MAKE) add-k8s-trust K8S_TRUST_IP=$$_K8S_IP PROD=$(PROD) && \
	$(MAKE) -C ../kubernetes up \
		OVERRIDES_FILE=$(shell pwd)/../kubernetes/k8s-trust-Trust_K8s.yaml \
		PROD=$(PROD) && \
	echo "" && \
	echo "=== K8s Trust Status ===" && \
	$(MAKE) -C ../kubernetes status PROD=$(PROD) && \
	echo "" && \
	echo "✅ full-deploy-with-k8s complete (PROD=$(PROD))." && \
	echo "   EC2 trust status was verified in the full-deploy step above." && \
	echo "   K8S_TRUST_IP: $$_K8S_IP (FL-server NLB rule applied)"
