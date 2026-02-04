# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
export TF_VAR_ec2_public_key_path=/home/${USER}/.ssh/host-aws.pub
export TF_VAR_flip_user_pool_name=flip-user-pool
export TF_VAR_flip_cognito_client=flip-cognito-client
export TF_VAR_FLIP_BUCKET_NAME=${FLIP_BUCKET_NAME}
export TF_VAR_flip_cognito_admin_email=flip@gmail.com
export TF_VAR_flip_cognito_researcher_email=rafaelagd@gmail.com
export TF_VAR_NET_ENDPOINTS=${NET_ENDPOINTS}
export TF_VAR_POSTGRES_DB=${POSTGRES_DB}
export TF_VAR_AES_KEY_BASE64=${AES_KEY_BASE64}
export TF_VAR_POSTGRES_USER=${POSTGRES_USER}
export TF_VAR_DB_PORT=${DB_PORT}
export TF_VAR_UI_PORT=${UI_PORT}
export TF_VAR_API_PORT=${API_PORT}
export TF_VAR_FL_API_PORT=${FL_API_PORT}
export TF_VAR_TRUST_API_PORT=${TRUST_API_PORT}
export TF_VAR_XNAT_PORT=${XNAT_PORT_TRUST_1}
export TF_VAR_PACS_UI_PORT=${PACS_UI_PORT_TRUST_1}
export TF_VAR_AWS_REGION=${AWS_REGION}
export TF_VAR_VPC_NAME=${VPC_NAME}
export TF_VAR_ADMIN_USER_PASSWORD=${ADMIN_USER_PASSWORD}
export TF_VAR_SES_VERIFIED_EMAIL=${SES_VERIFIED_EMAIL}
export TF_VAR_flip_alb_subdomain=${ALB_SUBDOMAIN}

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
	@echo "1️⃣  S3 Bucket..."
	@BUCKET_NAME=$(TF_VAR_FLIP_BUCKET_NAME); \
	if [ -n "$$BUCKET_NAME" ]; then \
		if aws s3api head-bucket --bucket "$$BUCKET_NAME" --profile $(AWS_PROFILE) 2>/dev/null; then \
			terraform import aws_s3_bucket.flip_bucket "$$BUCKET_NAME" 2>/dev/null || echo "   ✓ S3 bucket"; \
			terraform import aws_s3_bucket_cors_configuration.flip_bucket_cors "$$BUCKET_NAME" 2>/dev/null || echo "   ✓ S3 CORS"; \
		fi; \
	fi
	@echo ""
	@echo "2️⃣  Secrets Manager..."
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
	@echo "3️⃣  Cognito..."
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
		terraform import aws_cognito_user.admin_user "$$POOL_ID/flip@gmail.com" 2>/dev/null || echo "   ✓ Admin user"; \
		terraform import aws_cognito_user.researcher_user "$$POOL_ID/rafaelagd@gmail.com" 2>/dev/null || echo "   ✓ Researcher user"; \
	fi
	@echo ""
	@echo "4️⃣  SES Email Identity..."
	@if aws ses list-identities --profile $(AWS_PROFILE) --query 'Identities' --output text | grep -q "$(TF_VAR_SES_VERIFIED_EMAIL)"; then \
		terraform import aws_ses_email_identity.flip_sender "$(TF_VAR_SES_VERIFIED_EMAIL)" 2>/dev/null || echo "   ✓ SES Email Identity"; \
	fi
	@echo "✅ Persistent resources imported!"

.PHONY: plan
plan:
	@echo "📋 Planning Terraform changes..."
	@terraform plan -out=plan.tfplan

.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure
	@echo "🛠️ Applying Terraform configuration..."
	@terraform apply plan.tfplan

.PHONY: ssh-config
ssh-config: ## Update local .ssh/config for flip contexts
	@echo "⚙️  Configure .ssh/config for flip context..."
	@set -x AWS_PROFILE ${AWS_PROFILE}
	@terraform output
	@python3 update_ssh_config.py --aws-profile ${AWS_PROFILE} || true
	@echo "✅ .ssh/config updated!"

.PHONY: update-env
update-env: ## Update environment file with Terraform outputs
	@echo "🔄 Updating environment file with infrastructure values..."
	@python3 update_env.py $(MAIN_ENV_FILE)

.PHONY: ansible-init
ansible-init: ## Initialize Ansible configuration on EC2 instances (if failed try again for 3 times)
	@echo "☁️🔗 Initiate ansible and install docker on both EC2 instances"
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@uv run ansible-playbook site.yml -e flip_aicentre_bucket=${AICENTRE_BUCKET_NAME}

.PHONY: deploy-centralhub
deploy-centralhub: ## Deploy FLIP containers to flip context with environment from Terraform
	@echo "📋 Loading environment variables from Terraform outputs..."
	@echo "👷 Create docker context if not exists, pointing to flip to ssh://flip"
	@docker context create flip --docker "host=ssh://flip" || echo "   Context 'flip' already exists"
	@# Switch to flip context
	@echo "🔄 Switching to flip Docker context..."
	@docker context use flip
	@echo "🐳 Deploying containers..."
	@$(MAKE) -C ../../../ up-centralhub-stag PROD=stag
	@echo "✅ Deployment complete!"
	@docker context use default

.PHONY: deploy-trust
deploy-trust: update-env ## Deploy trust relationships after central hub is deployed
	@echo "⚙️  Configure .ssh/config for flip context..."
	@set -x AWS_PROFILE ${AWS_PROFILE}
	@terraform output
	@python3 update_ssh_config.py --aws-profile ${AWS_PROFILE} || true
	@echo "✅ .ssh/config updated!"
	@echo "👷 Create docker context if not exists, pointing to flip-trust to ssh://flip-trust"
	@docker context create flip-trust --docker "host=ssh://flip-trust" || echo "   Context 'flip-trust' already exists"
	@echo "🔄 Switching to flip-trust Docker context..."
	@docker context use flip-trust
	@echo "🐳 Deploying containers to Trust EC2 ..."
	docker swarm init || echo "   Swarm already initialized"
	@echo "🚀 Deploying Trust services..."
	$(MAKE) -C ../../../ up-trust-stag PROD=stag
	@echo "✅ Trust deployment complete!"

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
	@python3 check_status.py

check:
	@echo "🔍 Checking Terraform state..."
	@terraform state list
	@echo ""
	@terraform output
	@python3 check_status.py || true


.PHONY: destroy
destroy: ## Destroy infrastructure while preserving Cognito and Secrets Manager resources
	@echo "💥 Destroying ephemeral infrastructure..."
	@terraform destroy -auto-approve \
		-target=module.flip_vpc \
		-target=module.ec2_security_group \
		-target=module.rds_security_group \
		-target=aws_db_subnet_group.flip_db_subnet_group \
		-target=module.flip_db \
		-target=module.alb \
		-target=module.alb_security_group \
		-target=module.ec2_role \
		-target=aws_iam_instance_profile.ec2_profile \
		-target=aws_key_pair.flip_keypair \
		-target=aws_instance.ec2_instance \
		-target=aws_cloudwatch_log_group.flip_log_group \
		-target=aws_iam_role_policy.ec2_secret \
		-target=aws_ses_template.flip_access_request \
		-target=aws_ses_template.flip_xnat_credentials \
		-target=module.trust_ec2 \
		-target=aws_key_pair.host_key \
		-target=local_file.env
	@echo "✅ Targeted destroy complete!"

.PHONY: help
help: ## Show this help message
	@echo "FLIP AWS Deployment Commands:"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$0, $$1, $$2}'

.PHONY: full-deploy
full-deploy: github-login aws-login init import-persistent plan apply update-env plan apply ssh-config ansible-init deploy-centralhub deploy-trust status
