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
export TF_VAR_ec2_public_key_path=/home/${USER}/.ssh/host-aws.pub
export TF_VAR_flip_user_pool_name=flip-user-pool
export TF_VAR_flip_cognito_client=flip-cognito-client
export TF_VAR_FLIP_BUCKET_NAME=${FLIP_BUCKET_NAME}
export TF_VAR_AICENTRE_BUCKET_NAME=${AICENTRE_BUCKET_NAME}
export TF_VAR_flip_cognito_admin_email=aicentreflip@gmail.com
export TF_VAR_flip_cognito_researcher_email=rafaelagd@gmail.com
export TF_VAR_POSTGRES_DB=${POSTGRES_DB}
export TF_VAR_AES_KEY_BASE64=${AES_KEY_BASE64}
export TF_VAR_POSTGRES_USER=${POSTGRES_USER}
export TF_VAR_DB_PORT=${DB_PORT}
export TF_VAR_UI_PORT=${UI_PORT}
export TF_VAR_API_PORT=${API_PORT}
export TF_VAR_FL_API_PORT=${FL_API_PORT}
export TF_VAR_FL_SERVER_PORT=${FL_SERVER_PORT}
export TF_VAR_TRUST_API_PORT=${TRUST_API_PORT}
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
