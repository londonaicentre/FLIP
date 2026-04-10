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
MAIN_ENV_FILE=../../../.env.stag
endif

ifneq ("$(wildcard $(MAIN_ENV_FILE))","")
include $(MAIN_ENV_FILE)
export $(shell sed 's/=.*//' $(MAIN_ENV_FILE))
endif

# If PROD=true, check that the AWS_PROFILE=FlipDeveloperAccess-046651569599
ifeq ($(PROD),true)
ifneq ($(AWS_PROFILE),FlipDeveloperAccess-046651569599)
$(error "For production deployments, AWS_PROFILE must be set to 'FlipDeveloperAccess-046651569599'")
endif
endif

## SETTING VARIABLES FOR TERRAFORM
export TF_VAR_max_azs=2
export TF_VAR_vpc_cidr=10.0.0.0/16
export TF_VAR_public_subnets=["10.0.1.0/24", "10.0.2.0/24"]
export TF_VAR_private_subnets=["10.0.101.0/24", "10.0.102.0/24"]
export TF_VAR_flip_keypair=~/.ssh/host-aws
export TF_VAR_ec2_public_key_path=$(HOME)/.ssh/host-aws.pub
export TF_VAR_flip_user_pool_name=flip-user-pool
export TF_VAR_flip_cognito_client=flip-cognito-client
export TF_VAR_FLIP_BUCKET_NAME=${FLIP_BUCKET_NAME}
export TF_VAR_AICENTRE_BUCKET_NAME=${AICENTRE_BUCKET_NAME}
export TF_VAR_flip_cognito_admin_email=aicentreflip@gmail.com
export TF_VAR_flip_cognito_researcher_email=rafaelagd@gmail.com
export TF_VAR_POSTGRES_DB=${POSTGRES_DB}
export TF_VAR_AES_KEY_BASE64=${AES_KEY_BASE64}
export TF_VAR_TRUST_API_KEY_HASHES=${TRUST_API_KEY_HASHES}
export TF_VAR_INTERNAL_SERVICE_KEY_HASH=${INTERNAL_SERVICE_KEY_HASH}
export TF_VAR_POSTGRES_USER=${POSTGRES_USER}
export TF_VAR_DB_PORT=${DB_PORT}
export TF_VAR_UI_PORT=${UI_PORT}
export TF_VAR_API_PORT=${API_PORT}
export TF_VAR_FL_API_PORT=${FL_API_PORT}
export TF_VAR_FL_SERVER_PORT=${FL_SERVER_PORT}
export TF_VAR_XNAT_PORT=${XNAT_PORT_TRUST_1}
export TF_VAR_PACS_UI_PORT=${PACS_UI_PORT_TRUST_1}
export TF_VAR_AWS_REGION=${AWS_REGION}
export TF_VAR_VPC_NAME=${VPC_NAME}
export TF_VAR_ADMIN_USER_PASSWORD=${ADMIN_USER_PASSWORD}
export TF_VAR_SES_VERIFIED_EMAIL=${SES_VERIFIED_EMAIL}
export TF_VAR_flip_alb_subdomain=${ALB_SUBDOMAIN}
export TF_VAR_flip_nlb_subdomain=${NLB_SUBDOMAIN}

prettier:
	terraform fmt -recursive
	npx prettier --write "**/*.{yml,yaml}"

.PHONY: create-backend
create-backend: ## Create Terraform backend S3 bucket - Only run once
	@echo "🚀 Creating Terraform backend resources..."
	@./create_backend.sh

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
	@terraform init -backend-config="bucket=$(FLIP_TFSTATE_BUCKET_NAME)" -reconfigure


.PHONY: import-persistent
import-persistent: ## Import persistent resources (S3, Secrets, Cognito) to prevent replacement
	@echo "🔄 Importing persistent resources..."
	@echo "1️⃣  FLIP S3 Bucket..."
	@BUCKET_NAME=$(TF_VAR_FLIP_BUCKET_NAME); \
	if [ -n "$$BUCKET_NAME" ]; then \
		if aws s3api head-bucket --bucket "$$BUCKET_NAME" --profile $(AWS_PROFILE) 2>/dev/null; then \
			echo "   Found bucket: $$BUCKET_NAME"; \
			if terraform state show aws_s3_bucket.flip_bucket >/dev/null 2>&1; then \
				echo "   ✓ FLIP S3 bucket (already in state)"; \
			else \
				echo "   Importing bucket into state..."; \
				terraform import aws_s3_bucket.flip_bucket "$$BUCKET_NAME" 2>&1 | grep -v "Resource already managed" || echo "   ✓ FLIP S3 bucket imported"; \
			fi; \
			terraform import aws_s3_bucket_cors_configuration.flip_bucket_cors "$$BUCKET_NAME" 2>/dev/null || echo "   ✓ FLIP S3 CORS"; \
			terraform import aws_s3_object.app_destination_bucket "$$BUCKET_NAME/app_destination_bucket/" 2>/dev/null || echo "   ✓ app_destination_bucket folder"; \
			terraform import aws_s3_object.model_files "$$BUCKET_NAME/model_files/" 2>/dev/null || echo "   ✓ model_files folder"; \
			terraform import aws_s3_object.uploaded_federated_data "$$BUCKET_NAME/uploaded_federated_data/" 2>/dev/null || echo "   ✓ uploaded_federated_data folder"; \
		else \
			echo "   ⚠️  Bucket $$BUCKET_NAME does not exist in AWS"; \
		fi; \
	fi
	@echo ""
	@echo "2️⃣  AI Centre S3 Bucket..."
	@AICENTRE_BUCKET_NAME=$(TF_VAR_AICENTRE_BUCKET_NAME); \
	if [ -n "$$AICENTRE_BUCKET_NAME" ]; then \
		if aws s3api head-bucket --bucket "$$AICENTRE_BUCKET_NAME" --profile $(AWS_PROFILE) 2>/dev/null; then \
			echo "   Found bucket: $$AICENTRE_BUCKET_NAME"; \
			if terraform state show aws_s3_bucket.aicentre_bucket >/dev/null 2>&1; then \
				echo "   ✓ AI Centre S3 bucket (already in state)"; \
			else \
				echo "   Importing bucket into state..."; \
				terraform import aws_s3_bucket.aicentre_bucket "$$AICENTRE_BUCKET_NAME" 2>&1 | grep -v "Resource already managed" || echo "   ✓ AI Centre S3 bucket imported"; \
			fi; \
			terraform import aws_s3_bucket_cors_configuration.aicentre_bucket_cors "$$AICENTRE_BUCKET_NAME" 2>/dev/null || echo "   ✓ AI Centre S3 CORS"; \
		else \
			echo "   ⚠️  Bucket $$AICENTRE_BUCKET_NAME does not exist in AWS - will be created"; \
		fi; \
	else \
		echo "   ⚠️  AICENTRE_BUCKET_NAME not set in environment"; \
	fi
	@echo ""
	@echo "3️⃣  Secrets Manager..."
	@SECRET_ARN=$$(aws secretsmanager list-secrets --include-planned-deletion --profile $(AWS_PROFILE) --query 'SecretList[?Name==`FLIP_API`].ARN' --output text 2>/dev/null); \
	if [ -n "$$SECRET_ARN" ]; then \
		DELETION_DATE=$$(aws secretsmanager describe-secret --secret-id FLIP_API --profile $(AWS_PROFILE) --query 'DeletedDate' --output text 2>/dev/null); \
		if [ -n "$$DELETION_DATE" ] && [ "$$DELETION_DATE" != "None" ]; then \
			echo "   Restoring scheduled-for-deletion secret..."; \
			aws secretsmanager restore-secret --secret-id FLIP_API --profile $(AWS_PROFILE) 2>/dev/null && echo "   ✅ Restored!"; \
		fi; \
		terraform import 'module.flip_api_secret.aws_secretsmanager_secret.this[0]' "$$SECRET_ARN" 2>/dev/null || echo "   ✓ Secret"; \
		VERSION_ID=$$(aws secretsmanager describe-secret --secret-id FLIP_API --profile $(AWS_PROFILE) --query 'VersionIdsToStages | keys(@) | [0]' --output text 2>/dev/null); \
		if [ -n "$$VERSION_ID" ]; then \
			terraform import 'module.flip_api_secret.aws_secretsmanager_secret_version.this[0]' "$$SECRET_ARN|$$VERSION_ID" 2>/dev/null || echo "   ✓ Secret version"; \
		fi; \
	fi
	@echo ""
	@echo "4️⃣  Cognito..."
	@POOL_ID=$$(aws cognito-idp list-user-pools --max-results 20 --profile $(AWS_PROFILE) --query 'UserPools[?Name==`flip-user-pool`].Id' --output text 2>/dev/null); \
	if [ -n "$$POOL_ID" ]; then \
		terraform import aws_cognito_user_pool.flip_user_pool "$$POOL_ID" 2>/dev/null || echo "   ✓ User pool"; \
		DOMAIN=$$(aws cognito-idp describe-user-pool --user-pool-id "$$POOL_ID" --profile $(AWS_PROFILE) --query 'UserPool.Domain' --output text 2>/dev/null); \
		if [ -n "$$DOMAIN" ] && [ "$$DOMAIN" != "None" ]; then \
			terraform import aws_cognito_user_pool_domain.main "$$DOMAIN" 2>/dev/null || echo "   ✓ Domain"; \
		fi; \
		terraform import random_string.cognito_domain "$$DOMAIN" 2>/dev/null || echo "   ✓ Random string"; \
		CLIENT_ID=$$(aws cognito-idp list-user-pool-clients --user-pool-id "$$POOL_ID" --profile $(AWS_PROFILE) --query 'UserPoolClients[0].ClientId' --output text 2>/dev/null); \
		if [ -n "$$CLIENT_ID" ]; then \
			terraform import aws_cognito_user_pool_client.client "$$POOL_ID/$$CLIENT_ID" 2>/dev/null || echo "   ✓ Client"; \
		fi; \
		terraform import aws_cognito_user.admin_user "$$POOL_ID/aicentreflip@gmail.com" 2>/dev/null || echo "   ✓ Admin user"; \
		terraform import aws_cognito_user.researcher_user "$$POOL_ID/rafaelagd@gmail.com" 2>/dev/null || echo "   ✓ Researcher user"; \
	fi
	@echo ""
	@echo "5️⃣  SES Email Identity..."
	@EXISTING_SES_EMAIL=$$(aws ses list-identities --profile $(AWS_PROFILE) --query 'Identities[0]' --output text 2>/dev/null); \
	if [ -n "$$EXISTING_SES_EMAIL" ] && [ "$$EXISTING_SES_EMAIL" != "None" ]; then \
		if [ "$$EXISTING_SES_EMAIL" = "$(TF_VAR_SES_VERIFIED_EMAIL)" ]; then \
			echo "   ✓ Existing SES identity matches configuration: $$EXISTING_SES_EMAIL"; \
			terraform import aws_ses_email_identity.flip_sender "$$EXISTING_SES_EMAIL" 2>/dev/null || echo "   ✓ SES Email Identity (already imported)"; \
		else \
			echo "   ⚠️  Existing SES identity ($$EXISTING_SES_EMAIL) does NOT match configuration ($(TF_VAR_SES_VERIFIED_EMAIL))"; \
			echo "   ℹ️  Skipping import - Terraform will destroy and recreate with correct email"; \
		fi; \
	else \
		echo "   ℹ️  No existing SES identity found - Terraform will create $(TF_VAR_SES_VERIFIED_EMAIL)"; \
	fi
	@echo ""
	@echo "6️⃣  ACM Certificate..."
	@CERT_ARN=$$(aws acm list-certificates --profile $(AWS_PROFILE) --region $(TF_VAR_AWS_REGION) --query "CertificateSummaryList[?DomainName=='$(TF_VAR_flip_alb_subdomain)'].CertificateArn" --output text 2>/dev/null); \
	if [ -n "$$CERT_ARN" ]; then \
		terraform import aws_acm_certificate.flip "$$CERT_ARN" 2>/dev/null || echo "   ✓ ACM certificate"; \
		terraform import aws_acm_certificate_validation.flip "$$CERT_ARN" 2>/dev/null || echo "   ✓ Certificate validation"; \
		VALIDATION_RECORDS=$$(aws acm describe-certificate --certificate-arn "$$CERT_ARN" --profile $(AWS_PROFILE) --region $(TF_VAR_AWS_REGION) --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text 2>/dev/null); \
		if [ -n "$$VALIDATION_RECORDS" ]; then \
			HOSTED_ZONE_ID=$$(aws route53 list-hosted-zones --profile $(AWS_PROFILE) --query "HostedZones[?Name=='$(TF_VAR_flip_alb_subdomain).'].Id" --output text 2>/dev/null | cut -d'/' -f3); \
			if [ -n "$$HOSTED_ZONE_ID" ]; then \
				RECORD_NAME=$$(aws acm describe-certificate --certificate-arn "$$CERT_ARN" --profile $(AWS_PROFILE) --region $(TF_VAR_AWS_REGION) --query 'Certificate.DomainValidationOptions[0].ResourceRecord.Name' --output text 2>/dev/null); \
				terraform import "aws_route53_record.cert_validation[\"$(TF_VAR_flip_alb_subdomain)\"]" "$${HOSTED_ZONE_ID}_$${RECORD_NAME}_CNAME" 2>/dev/null || echo "   ✓ Route53 validation record"; \
			fi; \
		fi; \
	fi
	@echo ""
	@echo "ℹ️  Note: Terraform state bucket ($(FLIP_TFSTATE_BUCKET_NAME)) is managed externally via create_backend.sh"
	@echo "✅ Persistent resources imported!"

.PHONY: plan
plan:
	@echo "📋 Planning Terraform changes..."
	@terraform plan -out=plan.tfplan

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

.PHONY: refresh
refresh: ## Refresh Terraform state to sync with actual AWS resources
	@echo "🔄 Refreshing Terraform state..."
	@terraform refresh
	@echo "✅ State refreshed!"



.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure
	@echo "🛠️ Applying Terraform configuration..."
	@terraform apply plan.tfplan

.PHONY: ssh-config
ssh-config: ## Update local .ssh/config for flip contexts
	@echo "⚙️  Configure .ssh/config for flip context..."
	@set -x AWS_PROFILE ${AWS_PROFILE}
	@terraform output
	@uv run ./update_ssh_config.py --aws-profile ${AWS_PROFILE} || true
	@echo "✅ .ssh/config updated!"

.PHONY: update-env
update-env: ## Update environment file with Terraform outputs
	@echo "🔄 Updating environment file with infrastructure values..."
	@uv run ./update_env.py $(MAIN_ENV_FILE)

.PHONY: ansible-init
ansible-init: ## Initialize Ansible configuration on EC2 instances (if failed try again for 3 times)
	@echo "☁️🔗 Initiate ansible and install docker on both EC2 instances"
	@test -n "$(FL_KIT_DATE)" || (echo "FL_KIT_DATE must be set in $(MAIN_ENV_FILE)"; exit 1)
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@uv run ansible-playbook site.yml -e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) -e fl_kit_date=$(FL_KIT_DATE) -e pgdata_archive=trust1_pgdata_20260106.tar.gz

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
	@$(MAKE) -C ../../../ up-centralhub-stag PROD=stag
	@echo "✅ Deployment complete!"
	@docker context use default

# Default SSH user for on-premises trust hosts (override with LOCAL_TRUST_SSH_USER=<user>)
LOCAL_TRUST_SSH_USER ?= ubuntu
# Trust kit for the local (on-prem) trust FL client (default Trust_2; EC2 trust uses Trust_1)
LOCAL_TRUST_NAME ?= Trust_2


.PHONY: deploy-trust
deploy-trust: update-env ssh-config ## Deploy trust relationships after central hub is deployed
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
	$(MAKE) -C ../../../ up-trust-stag PROD=stag
	@echo "✅ Trust deployment complete!"

.PHONY: add-local-trust
add-local-trust: ## Provision an on-prem Trust host and register it with the Central Hub
	## LOCAL_TRUST_SSH_KEY is optional. When omitted, runs locally (no SSH) — use this
	## when the trust host IS the operator workstation or is not reachable via its public IP.
	## Local mode configures the host and prints the sudo commands to run (UFW rules, kit deploy).
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make add-local-trust LOCAL_TRUST_IP=<public-ip> [LOCAL_TRUST_SSH_KEY=<path>]"; echo "   Omit LOCAL_TRUST_SSH_KEY to provision the local machine (no SSH)."; echo ""; exit 1)
	@echo "🔧 Provisioning on-premises Trust host at $(LOCAL_TRUST_IP)..."
	@uv sync
	@uv run ansible-galaxy install -r ../../../deploy/providers/local/requirements.yml
	@CH_IP="$$(terraform output -raw Ec2PublicIp)" && \
	if [ -n "$(LOCAL_TRUST_SSH_KEY)" ]; then \
		echo "📡 Connecting via SSH (key: $(LOCAL_TRUST_SSH_KEY))..."; \
		uv run ansible-playbook \
			-i "$(LOCAL_TRUST_IP)," \
			-u "$(LOCAL_TRUST_SSH_USER)" \
			--private-key "$(LOCAL_TRUST_SSH_KEY)" \
			../../../deploy/providers/local/site_local_trust.yml \
			-e trust_public_ip=$(LOCAL_TRUST_IP) \
			-e ch_public_ip="$$CH_IP"; \
	else \
		echo "💻 Running locally (no SSH — operator machine is the trust host)..."; \
		if [ -z "$$ANSIBLE_BECOME_PASS" ]; then \
			echo ""; \
			echo "❌  ANSIBLE_BECOME_PASS is not set. Run this in your fish terminal first:"; \
			echo ""; \
			echo "     set -x ANSIBLE_BECOME_PASS (read -s -P 'Sudo password: ')"; \
			echo ""; \
			echo "   Then re-run this make target."; \
			echo ""; \
			exit 1; \
		fi; \
		echo "🔐 Authenticating sudo and creating temporary NOPASSWD entry..."; \
		CURR_USER=$$(id -un); \
		echo "$$ANSIBLE_BECOME_PASS" | sudo -S sh -c \
			"echo '$$CURR_USER ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/flip-ansible-local && chmod 440 /etc/sudoers.d/flip-ansible-local" \
			2>/dev/null || { echo "❌  sudo authentication failed — check your password."; exit 1; }; \
		echo "✅ Authenticated. Running Ansible playbook..."; \
		trap 'sudo -n rm -f /etc/sudoers.d/flip-ansible-local' EXIT INT TERM; \
		ANSIBLE_BECOME_PASS= uv run ansible-playbook \
			-i "localhost," \
			-e ansible_connection=local \
			-e ansible_python_interpreter=/usr/bin/python3 \
			../../../deploy/providers/local/site_local_trust.yml \
			-e trust_public_ip=$(LOCAL_TRUST_IP) \
			-e ch_public_ip="$$CH_IP"; \
		PLAY_RC=$$?; \
		exit $$PLAY_RC; \
	fi
	@echo "📦 Downloading $(LOCAL_TRUST_NAME) FL participant kit from S3..."
	@LOCAL_TRUST_KIT_DIR=/tmp/$(LOCAL_TRUST_NAME)_kit && \
	  rm -rf $$LOCAL_TRUST_KIT_DIR && mkdir -p $$LOCAL_TRUST_KIT_DIR && \
	  AWS_PROFILE=$(AWS_PROFILE) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FL_KIT_DATE)/net-1/services/$(LOCAL_TRUST_NAME)/ \
	    $$LOCAL_TRUST_KIT_DIR && \
	  if [ -n "$(LOCAL_TRUST_SSH_KEY)" ]; then \
	    ssh -i $(LOCAL_TRUST_SSH_KEY) $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP) \
	      "mkdir -p /opt/flip/services/Trust_$(LOCAL_TRUST_NAME)/startup /opt/flip/services/$(LOCAL_TRUST_NAME)/local /opt/flip/services/$(LOCAL_TRUST_NAME)/transfer" && \
	    rsync -av --delete -e "ssh -i $(LOCAL_TRUST_SSH_KEY)" \
	      $$LOCAL_TRUST_KIT_DIR/startup/ $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP):/opt/flip/services/$(LOCAL_TRUST_NAME)/startup/ && \
	    rsync -av --delete -e "ssh -i $(LOCAL_TRUST_SSH_KEY)" \
	      $$LOCAL_TRUST_KIT_DIR/local/ $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP):/opt/flip/services/$(LOCAL_TRUST_NAME)/local/; \
	  else \
	    echo ""; \
	    echo "  # 3. Deploy the $(LOCAL_TRUST_NAME) FL participant kit (kit staged at $$LOCAL_TRUST_KIT_DIR):"; \
	    echo "  sudo mkdir -p /opt/flip/services/$(LOCAL_TRUST_NAME)/startup /opt/flip/services/$(LOCAL_TRUST_NAME)/local /opt/flip/services/$(LOCAL_TRUST_NAME)/transfer"; \
	    echo "  sudo rsync -av --delete $$LOCAL_TRUST_KIT_DIR/startup/ /opt/flip/services/$(LOCAL_TRUST_NAME)/startup/"; \
	    echo "  sudo rsync -av --delete $$LOCAL_TRUST_KIT_DIR/local/ /opt/flip/services/$(LOCAL_TRUST_NAME)/local/"; \
	  fi && \
	  rm -rf $$LOCAL_TRUST_KIT_DIR && \
	  echo "✅ $(LOCAL_TRUST_NAME) FL kit deployed"
	@echo "🔐 Updating AWS security group rules..."
	@TF_VAR_local_trust_public_ip=$(LOCAL_TRUST_IP) terraform plan \
		-target=aws_security_group_rule.local_trust_fl_server \
		-target=aws_security_group_rule.local_trust_fl_admin \
		-out=local-trust.tfplan
	@TF_VAR_local_trust_public_ip=$(LOCAL_TRUST_IP) terraform apply local-trust.tfplan
	@rm -f local-trust.tfplan
	@echo ""
	@echo "✅ On-premises Trust host provisioned!"
	@echo ""
	@echo "⚠️  REMAINING MANUAL STEPS:"
	@echo "   1. On the trust host — start the trust stack: cd trust && PROD=$(PROD) make up-local-trust-stag"
	@echo "   2. Verify the trust can poll the hub (check trust-api logs for successful task polling)"

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
destroy: backup-eips ## Destroy ephemeral infrastructure (EC2, RDS, VPC) - preserves Cognito, Secrets, S3, EIPs
	@echo ""
	@echo "⚠️  SELECTIVE DESTRUCTION - PRESERVING:"
	@echo "   ✓ Cognito user pool and identities"
	@echo "   ✓ AWS Secrets Manager (FLIP_API secret)"
	@echo "   ✓ S3 buckets (FLIP + AI Centre data)"
	@echo "   ✓ ACM certificates and Route53 records"
	@echo "   ✓ Elastic IPs (for Central Hub and Trust EC2)"
ifeq ($(PROD),true)
	@echo "   ✓ VPC (Transit Gateway attachment — production)"
endif
	@echo ""
	@echo "🗑️  DESTROYING:"
	@echo "   • EC2 instances (flip, trust) - instances only, EIPs preserved"
	@echo "   • RDS database (PostgreSQL)"
	@echo "   • Security groups"
ifneq ($(PROD),true)
	@echo "   • VPC"
endif
	@echo "   • ALB/NLB load balancers"
	@echo "   • IAM roles and policies"
	@echo "   • CloudWatch logs"
	@echo ""
	@bash -c 'read -p "Type '\''yes'\'' to confirm destruction: " confirm && [ "$$confirm" = "yes" ] || { echo "Aborted."; exit 1; }'
	@echo ""
	@bash scripts/destroy-preserve-eips.sh
	@echo ""
	@echo "⚠️  IMPORTANT: Review EIP state before re-applying:"
	@echo "   make list-eips   # Check that your EIPs are still allocated"

.PHONY: help
help: ## Show this help message
	@echo "FLIP AWS Deployment Commands:"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$0, $$1, $$2}'

.PHONY: generate-keys
generate-keys: ## Generate trust API keys for trusts that still have placeholder values
	@echo "🔑 Generating trust API keys (skipping already-generated)..."
	@$(MAKE) -C ../../../flip-api generate-trust-api-keys ENV_FILE=$(abspath $(MAIN_ENV_FILE)) $(if $(FORCE),FORCE=true)

.PHONY: generate-internal-service-key
generate-internal-service-key: ## Generate a new internal service key (fl-server → flip-api auth)
	@echo "🔑 Generating internal service key..."
	@$(MAKE) -C ../../../flip-api generate-internal-service-key ENV_FILE=$(abspath $(MAIN_ENV_FILE))

.PHONY: full-deploy
full-deploy: ## Full AWS deployment: provision infrastructure, deploy cloud Trust + Central Hub
	## Step order:
	## 1. login + init + import: auth, Terraform init, import any pre-existing resources
	## 2. generate-keys + generate-internal-service-key: generate all API keys
	## 3. plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, secrets)
	## 4. update-env: refresh .env with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure SSH aliases and provision EC2s with Docker
	## 6. deploy-centralhub + deploy-trust + status: deploy containers and verify
full-deploy: github-login aws-login init import-persistent generate-keys generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub deploy-trust status

.PHONY: full-deploy-stag
full-deploy-stag: ## Full staging deployment with on-prem local trust (requires LOCAL_TRUST_IP=<public-ip>)
	## Step order:
	## 1. login + init + import: auth, Terraform init, import pre-existing persistent resources
	## 2. generate-keys + generate-internal-service-key: generate all API keys
	## 3. plan+apply (PROD=stag): provision all AWS infrastructure (EC2, RDS, VPC, ALB, secrets)
	## 4. update-env: refresh .env.stag with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 5. ssh-config + ansible-init: configure SSH aliases, provision EC2s with Docker + FL kit
	## 6. deploy-centralhub + deploy-trust: deploy Central Hub and cloud Trust containers
	## 7. add-local-trust LOCAL_TRUST_IP=<ip>: configure UFW,
	##    deploy local $(LOCAL_TRUST_NAME) FL kit, and update AWS security groups
	## 8. status: verify all services healthy
	## NOTE: After this completes you must manually:
	##   - Start local trust stack: cd ../../../trust && env PROD=stag make up-local-trust-stag
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make full-deploy-stag LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
full-deploy-stag: github-login aws-login init import-persistent generate-keys generate-internal-service-key plan apply update-env ssh-config ansible-init deploy-centralhub deploy-trust add-local-trust status

.PHONY: full-deploy-stag-hybrid
full-deploy-stag-hybrid: ## Full staging hybrid deployment including local trust provisioning
	## This wraps full-deploy-stag and then redeploys the Central Hub.
	## You still need to start the local trust stack on the trust host.
	## If LOCAL_TRUST_IP is not provided, it will be auto-detected using curl.
	@if [ -z "$(LOCAL_TRUST_IP)" ]; then \
		echo "🔍 Detecting public IP for LOCAL_TRUST_IP..."; \
		LOCAL_TRUST_IP=$$(curl -s https://api.ipify.org) && \
		echo "✅ Detected LOCAL_TRUST_IP: $$LOCAL_TRUST_IP" || (echo "❌  Failed to detect IP. Provide LOCAL_TRUST_IP manually."; echo "   Usage: make full-deploy-stag-hybrid LOCAL_TRUST_IP=<public-ip>"; exit 1); \
	else \
		LOCAL_TRUST_IP="$(LOCAL_TRUST_IP)"; \
	fi && \
	$(MAKE) full-deploy-stag LOCAL_TRUST_IP=$$LOCAL_TRUST_IP LOCAL_TRUST_SSH_KEY="$(LOCAL_TRUST_SSH_KEY)" LOCAL_TRUST_SSH_USER="$(LOCAL_TRUST_SSH_USER)" LOCAL_TRUST_NAME="$(LOCAL_TRUST_NAME)" && \
	$(MAKE) deploy-centralhub PROD=stag && \
	echo "" && \
	echo "✅ Hybrid staging deployment complete." && \
	echo "➡️  Final manual step: start the local trust stack on the trust host:" && \
	echo "   cd ../../../trust && env PROD=stag make up-local-trust-stag"
