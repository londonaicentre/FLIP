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
export TF_VAR_XNAT_PORT=${XNAT_PORT_TRUST_1}
export TF_VAR_PACS_UI_PORT=${PACS_UI_PORT_TRUST_1}
export TF_VAR_AWS_REGION=${AWS_REGION}
export TF_VAR_VPC_NAME=${VPC_NAME}
export TF_VAR_ADMIN_USER_PASSWORD=${ADMIN_USER_PASSWORD}
export TF_VAR_SES_VERIFIED_EMAIL=${SES_VERIFIED_EMAIL}
export TF_VAR_flip_alb_subdomain=${ALB_SUBDOMAIN}
export TF_VAR_flip_nlb_subdomain=${NLB_SUBDOMAIN}
export TF_VAR_FLIP_UI_BUCKET_NAME=${FLIP_UI_BUCKET_NAME}
# flip-api / fl-* env vars consumed by ECS task definitions (locals.tf).
# These mirror compose.production.yml so the running container sees the same
# values whether it runs on EC2+compose or ECS Fargate.
export TF_VAR_TRUST_API_KEY_HEADER=${TRUST_API_KEY_HEADER}
export TF_VAR_INTERNAL_SERVICE_KEY_HEADER=${INTERNAL_SERVICE_KEY_HEADER}
export TF_VAR_FL_ADMIN_DIRECTORY=${FL_ADMIN_DIRECTORY}
export TF_VAR_ENFORCE_MFA=${ENFORCE_MFA}
CENTRALHUB_IMAGES = \
	$(DOCKER_REGISTRY)flip-api:$(DOCKER_TAG)

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

prettier:
	terraform fmt -recursive
	npx prettier --write "**/*.{yml,yaml}"

.PHONY: create-backend
create-backend: ## Create Terraform backend S3 bucket - Only run once
	@echo "🚀 Creating Terraform backend resources..."
	@TFSTATE_BUCKET_NAME="$(FLIP_TFSTATE_BUCKET_NAME)" TFSTATE_KEY="flip/terraform.tfstate" TFSTATE_REGION="$(AWS_REGION)" ./scripts/create-backend.sh

.PHONY: aws-login
aws-login: ## Login to AWS SSO (required before terraform operations)
	@echo "🔐 Logging into AWS SSO..."
	@aws sso login --profile $(AWS_PROFILE)
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
	@terraform init \
		-backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" \
		-backend-config="region=$(AWS_REGION)" \
		-reconfigure


.PHONY: import-persistent
import-persistent: ## Import persistent resources (S3, Secrets, Cognito) to prevent replacement
	@bash scripts/import-resources.sh


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
	@aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/model_files/uploaded/ \
		s3://$(FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME)/uploaded/
	@echo "  • FL results     → s3://$(FLIP_FL_RESULTS_BUCKET_NAME)/"
	@aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/uploaded_federated_data/ \
		s3://$(FLIP_FL_RESULTS_BUCKET_NAME)/
	@echo "  • FL base apps   → s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/base-application/"
	@aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/base-application/ \
		s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/base-application/
	@echo "  • FL app dests   → s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/app_destinations/"
	@aws s3 sync \
		s3://$(FLIP_BUCKET_NAME)/app_destination_bucket/ \
		s3://$(FLIP_APP_BUNDLES_BUCKET_NAME)/app_destinations/
	@echo "📦 Sync calls complete. Run \`make verify-flip-bucket-migration\` to confirm parity before decommissioning."


.PHONY: verify-flip-bucket-migration
verify-flip-bucket-migration: ## Parity-check the four migrated prefixes (every src key must exist on dst; extras on dst are fine)
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
	@bash scripts/force-unlock.sh $(LOCK_ID)

.PHONY: plan
plan: ## Plan Terraform changes (with environment file loaded)
	@echo "📋 Planning Terraform changes..."
	@terraform plan -out=plan.tfplan -lock=${LOCK}

.PHONY: plan-cert
plan-cert: ## Plan only the ACM certificate (step 1 of 2 for HTTPS)
	@echo "📋 Planning ACM certificate..."
	@terraform plan -target=aws_acm_certificate.flip -out=plan-cert.tfplan

.PHONY: apply-cert
apply-cert: ## Apply only the ACM certificate (step 1 of 2 for HTTPS)
	@echo "🔒 Applying ACM certificate (Step 1/2)..."
	@terraform apply -target=aws_acm_certificate.flip -auto-approve
	@echo "✅ Certificate created! Now run 'make plan' and 'make apply' to deploy DNS validation."

.PHONY: destroy-cert
destroy-cert: ## Destroy only the ACM certificate (use with caution - will break HTTPS)
	@echo "💥 Destroying ACM certificate (Step 1/2)..."
	@terraform destroy -target=aws_acm_certificate.flip -target=aws_acm_certificate_validation.flip -target=aws_route53_record.cert_validation
	@echo "✅ Certificate destroyed! Now run 'make plan' and 'make apply' to remove DNS validation."

.PHONY: plan-cloudfront-certs
plan-cloudfront-certs: ## Plan the CloudFront viewer cert (us-east-1)
	@echo "📋 Planning CloudFront viewer certificate..."
	@terraform plan \
		-target=aws_acm_certificate.flip_cloudfront \
		-out=plan-cloudfront-certs.tfplan

.PHONY: apply-cloudfront-certs
apply-cloudfront-certs: ## Apply the CloudFront viewer cert (us-east-1)
	@echo "🔒 Applying CloudFront viewer certificate (Step 1/2 for CloudFront)..."
	@terraform apply -target=aws_acm_certificate.flip_cloudfront -auto-approve
	@echo "✅ Certificate created. Run 'make plan' and 'make apply' to deploy DNS validation + CloudFront."

.PHONY: refresh
refresh: ## Refresh Terraform state to sync with actual AWS resources
	@echo "🔄 Refreshing Terraform state..."
	@terraform refresh
	@echo "✅ State refreshed!"


.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure (with environment file loaded)
	@echo "🛠️ Applying Terraform configuration..."
	@terraform apply plan.tfplan

.PHONY: forward-trust
forward-trust: ## SSM port-forward all Trust web UIs and API swagger docs (Ctrl+C to stop)
	@bash scripts/forward-trust-all.sh

.PHONY: ssh-config
ssh-config: check-ssm-ready ## Update local .ssh/config for flip contexts (requires SSM plugin)
	@echo "⚙️  Configure .ssh/config for flip context..."
	@AWS_PROFILE=${AWS_PROFILE} terraform output
	@AWS_PROFILE=${AWS_PROFILE} uv run ./update_ssm_ssh_config.py --aws-profile ${AWS_PROFILE}
	@echo "✅ .ssh/config updated!"

.PHONY: update-env
update-env: ## Update environment file with Terraform outputs
	@echo "🔄 Updating environment file with infrastructure values..."
	@uv run ./update_env.py $(MAIN_ENV_FILE)

.PHONY: ansible-init
ansible-init: ## Initialize Ansible configuration on EC2 instances (if failed try again for 3 times)
	@echo "☁️🔗 Initiate ansible and install docker on both EC2 instances"
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@uv run ansible-playbook site.yml \
		-e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) \
		-e fl_backend=$(FL_BACKEND) \
		-e fl_kit_date=$(FL_KIT_DATE) \
		-e pgdata_archive=trust1_pgdata_20260106.tar.gz

.PHONY: deploy-centralhub
deploy-centralhub: ssh-config ## Deploy FLIP containers to flip context with environment from Terraform
	@echo "🔍 Checking central hub images (tag: $(DOCKER_TAG))..."
	@missing=false; \
	for image in $(CENTRALHUB_IMAGES); do \
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
	  echo "   ✅ All central hub images found."; \
	fi
	@echo "📋 Loading environment variables from Terraform outputs..."
	@echo "👷 Create docker context if not exists, pointing to flip to ssh://flip"
	@docker context create flip --docker "host=ssh://flip" || echo "   Context 'flip' already exists"
	@# Switch to flip context
	@echo "🔄 Switching to flip Docker context..."
	@docker context use flip
	@echo "🧹 Stopping old containers and removing unused images to free disk space..."
	@docker stop $$(docker ps -aq) 2>/dev/null || true
	@docker rm $$(docker ps -aq) 2>/dev/null || true
	@docker image prune -af
	@echo "🐳 Deploying containers..."
	@$(MAKE) -C ../../../ up-centralhub-ec2 PROD=${PROD}
	@echo "✅ Deployment complete!"
	@docker context use default

.PHONY: deploy-ui
deploy-ui: ## Build flip-ui from the working tree and deploy it to S3 + invalidate CloudFront
	@echo "🖼️  Building flip-ui from working tree..."
	@# Uses build:deploy (vite build only). The type-checked `npm run build` fails
	@# on pre-existing TypeScript errors in the repo — tracked separately.
	@cd ../../../flip-ui && npm ci && npm run build:deploy
	@echo "🔧 Generating window.js from $(MAIN_ENV_FILE)..."
	@cd ../../../flip-ui && sh scripts/generate-window-js.sh > dist/js/window.js
	@echo "📦 Syncing static assets to s3://$(FLIP_UI_BUCKET_NAME)/ ..."
	@# Hashed bundles are content-addressed and safe to cache forever.
	@AWS_PROFILE=$(AWS_PROFILE) aws s3 sync ../../../flip-ui/dist/ s3://$(FLIP_UI_BUCKET_NAME)/ \
		--delete \
		--cache-control "public, max-age=31536000, immutable" \
		--exclude "index.html" --exclude "js/window.js"
	@# index.html and window.js must never be cached long — they reference the hashed bundles.
	@AWS_PROFILE=$(AWS_PROFILE) aws s3 cp ../../../flip-ui/dist/index.html s3://$(FLIP_UI_BUCKET_NAME)/index.html \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "text/html; charset=utf-8"
	@AWS_PROFILE=$(AWS_PROFILE) aws s3 cp ../../../flip-ui/dist/js/window.js s3://$(FLIP_UI_BUCKET_NAME)/js/window.js \
		--cache-control "no-cache, no-store, must-revalidate" \
		--content-type "application/javascript; charset=utf-8"
	@echo "🧹 Invalidating CloudFront..."
	@AWS_PROFILE=$(AWS_PROFILE) aws cloudfront create-invalidation \
		--distribution-id $$(AWS_PROFILE=$(AWS_PROFILE) terraform output -raw CloudfrontDistributionId) \
		--paths "/*" >/dev/null
	@echo "✅ deploy-ui complete."

# Identity of the on-prem trust kit being provisioned — selects its kit
# file trust/.env.<KIT> and its trust-local data dirs. In the FLIP prod
# environment this is Trust_2 (BDMS, TRUST_2_HOST=local). Template:
# trust/.env.Trust_2.production.example.
KIT ?= Trust_2
# FL participant kit slot the trust's fl-client mounts — names the
# services/<slot>/ dir in the S3 participant-kit bundle and under /opt/flip.
# Read from the trust's kit file (the single source of truth: the kit file's
# FL_KIT_SLOT is also what trust/Makefile mounts), not duplicated here.
FL_KIT_SLOT := $(shell sed -n 's/^FL_KIT_SLOT=//p' ../../../trust/.env.$(KIT) 2>/dev/null)

.PHONY: deploy-trust
deploy-trust: ssh-config update-env ## Deploy trust relationships after central hub is deployed
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
	@echo "🔄 Switching to flip-trust Docker context..."
	@docker context use flip-trust
	@echo "🐳 Deploying containers to Trust EC2 ..."
	docker swarm init || echo "   Swarm already initialized"
	@echo "🔧 Creating Docker networks on Trust EC2..."
	$(MAKE) -C ../../../ create-networks
	@echo "🧹 Stopping old containers and removing unused images to free disk space..."
	@docker stop $$(docker ps -aq) 2>/dev/null || true
	@docker rm $$(docker ps -aq) 2>/dev/null || true
	@docker image prune -af
	@echo "🚀 Deploying Trust services..."
	$(MAKE) -C ../../../ up-trust-ec2 PROD=${PROD}
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
	@[ -n "$(FL_KIT_SLOT)" ] || (echo ""; echo "❌  FL_KIT_SLOT not found — is trust/.env.$(KIT) present with FL_KIT_SLOT set?"; echo ""; exit 1)
	@TRUST_KIT_DIR=/tmp/$(FL_KIT_SLOT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR && \
	  AWS_PROFILE=$(AWS_PROFILE) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FL_KIT_DATE)/net-1/services/$(FL_KIT_SLOT)/ \
	    $$TRUST_KIT_DIR && \
	  echo "" && \
	  echo "  # Deploy the $(FL_KIT_SLOT) NVFLARE participant kit (staged at $$TRUST_KIT_DIR):" && \
	  echo "  sudo mkdir -p /opt/flip/services/$(FL_KIT_SLOT)/startup /opt/flip/services/$(FL_KIT_SLOT)/local /opt/flip/services/$(FL_KIT_SLOT)/transfer" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/startup/ /opt/flip/services/$(FL_KIT_SLOT)/startup/" && \
	  echo "  sudo rsync -av --delete $$TRUST_KIT_DIR/local/ /opt/flip/services/$(FL_KIT_SLOT)/local/" && \
	  echo "✅ $(FL_KIT_SLOT) NVFLARE kit staged"
else
	@TRUST_KIT_DIR=/tmp/$(KIT)_kit && \
	  rm -rf $$TRUST_KIT_DIR && mkdir -p $$TRUST_KIT_DIR/net-1 && \
	  AWS_PROFILE=$(AWS_PROFILE) aws s3 sync \
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
	@echo "   2. Start the trust stack: cd ../../.. && env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)"
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

.PHONY: down
down:
	@echo "🛑 Stopping all containers..."
	@docker context use flip
	$(MAKE) -C ../../../ down
	@docker context use flip-trust
	$(MAKE) -C ../../../ down
	@echo "✅ Containers stopped and removed"
	@docker context use default

.PHONY: status
status: ## Check deployment status
	@uv run ./check_status.py

check:
	@echo "🔍 Checking Terraform state..."
	@terraform state list
	@echo ""
	@terraform output
	@uv run ./check_status.py || true


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
	@PRESERVE_VPC=$(PRESERVE_VPC) bash scripts/destroy-selective.sh


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
register-trusts: ## Register the TRUST_<n>_* trusts on the hub and distribute kit files
	## Idempotent: for each TRUST_<n>_* in the env file, a one-off ECS task runs register_trust
	## (mints API + internal-service keys, claims an FL kit slot) unless the trust already exists.
	## The plaintext kit JSON is captured from the hub's CloudWatch log stream and written to a
	## per-trust kit file on the destination host (TRUST_<n>_HOST):
	##   - "ec2"   → scp to /opt/flip/trust/.env.<slot> on the trust EC2 via the flip-trust SSM context
	##   - "local" → write ../../../trust/.env.<slot> in the working tree
	## Pre-existing trusts produce no kit output (their hosts retain the original kit), so
	## re-running is safe.
	@bash scripts/register-trusts.sh

.PHONY: delete-trust
delete-trust: ## Soft-delete a trust on the hub (frees the name + FL kit slot for re-registration)
	## Runs flip_api.scripts.delete_trust as a one-off ECS task. Stamps disabled_at,
	## renames the row to <NAME>_archived_<unix_ts>, and frees its FL kit slot — keeps the
	## trust's historical data (cohort queries, projects) intact so we don't cascade-delete
	## through 6+ FK tables. Operator can then UI-add a new trust with the freed name.
	## Required: NAME=<trust-name> matching trust.name exactly.
	@[ -n "$(NAME)" ] || (echo "❌ NAME=<trust-name> is required"; exit 1)
	@NAME=$(NAME) bash scripts/delete-trust.sh

.PHONY: full-deploy
full-deploy: ## Full AWS deployment: provision infrastructure, deploy cloud Trust + Central Hub
	## Step order:
	## 1. login + init + import: auth, Terraform init, import any pre-existing resources
	## 2. generate-internal-service-key: mint the fl-server→hub key (idempotent)
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, secrets)
	## 4. update-env: refresh .env with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure SSH aliases and provision EC2s with Docker
	## 6. deploy-centralhub: bring up the Central Hub (seeds run, fl_kit_slot pool is populated)
	## 7. register-trusts: register every TRUST_<n>_* trust on the hub and distribute kit files
	## 8. deploy-trust + status: bring up cloud Trust container stack and verify
full-deploy: github-login aws-login init import-persistent generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub register-trusts deploy-trust status

.PHONY: full-deploy-with-local-trust
full-deploy-with-local-trust: ## Full AWS deployment (honours PROD) plus on-prem trust provisioning (requires LOCAL_TRUST_IP=<public-ip>)
	## Works against either PROD=true or PROD=stag — PROD is inherited from the environment.
	## Step order:
	## 1. login + init + import: auth, Terraform init, import pre-existing persistent resources
	## 2. generate-internal-service-key: mint the fl-server→hub key (idempotent)
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, ALB, secrets, on-prem trust NLB rule)
	## 4. update-env: refresh .env.{PROD} with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure SSH aliases, provision EC2s with Docker + FL kit
	## 6. deploy-centralhub: bring up the Central Hub
	## 7. register-trusts: register the TRUST_<n>_* trusts on the hub and distribute kit files
	## 8. deploy-trust + provision-local-trust + status: bring up cloud + on-prem trust hosts and verify
	## NOTE: After this completes you must manually:
	##   - Start the on-prem trust stack: cd ../../.. && env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)
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
	echo "➡️  Final manual step: start the on-prem trust stack on the trust host:" && \
	echo "   cd ../../.. && env PROD=$(PROD) make -C trust up-trust KIT=$(KIT)"
