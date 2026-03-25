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

ifeq ($(LOCK),)
LOCK=true
else
LOCK=false
endif

# Export PROD so child processes (e.g. check_status.py) can determine the environment
export PROD

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
export TF_VAR_create_central_hub_elastic_ip=${CREATE_CENTRAL_HUB_ELASTIC_IP}

CENTRALHUB_IMAGES = \
	$(DOCKER_REGISTRY)flip-ui:$(DOCKER_TAG) \
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
	@bash scripts/import-resources.sh


.PHONY: force-unlock
force-unlock: ## Force unlock Terraform state - provide LOCK_ID=<id> as argument
	@if [ -z "$(LOCK_ID)" ]; then \
		echo "❌ LOCK_ID not provided. Usage: make force-unlock LOCK_ID=<lock-id>"; \
		exit 1; \
	fi
	@bash scripts/manage-aws.sh force-unlock $(LOCK_ID)

.PHONY: delete-flip-keypair
delete-flip-keypair: ## Delete the flip-keypair from AWS (run before apply if it exists)
	@bash scripts/manage-aws.sh delete-keypair flip-keypair

.PHONY: list-eips
list-eips: ## List all Elastic IPs in the region
	@bash scripts/manage-aws.sh list-eips

.PHONY: release-unused-eips
release-unused-eips: ## Release unassociated Elastic IPs to free up quota
	@bash scripts/manage-aws.sh release-unused-eips

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

.PHONY: update-trust-secret
update-trust-secret: ## Update Secrets Manager with the current trust-ca.crt (run after cert regeneration)
	@echo "🔐 Updating Secrets Manager with the current trust-ca.crt..."
	@terraform apply -target='module.flip_api_secret.aws_secretsmanager_secret_version.this[0]' -auto-approve
	@echo "✅ Secrets Manager updated with new trust CA certificate!"

.PHONY: refresh
refresh: ## Refresh Terraform state to sync with actual AWS resources
	@echo "🔄 Refreshing Terraform state..."
	@terraform refresh
	@echo "✅ State refreshed!"

.PHONY: gen-trust-ec2-certs
gen-trust-ec2-certs: refresh ## Generate TLS certs for the cloud Trust EC2 using its Terraform-provisioned public IP
	@echo "🔑 Generating TLS certificates for cloud Trust EC2..."
	@TRUST_HOST="$$(terraform output -raw TrustEc2PublicIp)" \
		bash ../../../trust/certs/generate-trust-certs.sh
	@echo "📋 Copying CA cert to Terraform directory (picked up by Secrets Manager on next apply)..."
	@cp ../../../trust/certs/trust-ca.crt ./trust-ca.crt
	@echo "📦 Waiting for Trust EC2 to be fully initialized and SSH-ready..."
	@TRUST_IP="$$(terraform output -raw TrustEc2PublicIp)"; \
	echo "   Waiting for SSH to be available on $$TRUST_IP..."; \
	for i in $$(seq 1 60); do \
		ssh -i ~/.ssh/host-aws -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null ubuntu@$$TRUST_IP "exit 0" 2>/dev/null && echo "   ✓ SSH ready (attempt $$i)" && break; \
		if [ $$i -eq 60 ]; then echo "   ✗ SSH did not become available after 5 minutes."; exit 1; fi; \
		echo "   ... waiting (attempt $$i/60)"; \
		sleep 5; \
	done
	@echo "📤 Deploying server certificate and key to Trust EC2..."
	@TRUST_IP="$$(terraform output -raw TrustEc2PublicIp)"; \
	ssh -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null ubuntu@$$TRUST_IP "sudo mkdir -p /opt/flip/certs && sudo chown ubuntu:ubuntu /opt/flip/certs" && \
	scp -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null \
		../../../trust/certs/trust-server.crt \
		../../../trust/certs/trust-server.key \
		ubuntu@$$TRUST_IP:/opt/flip/certs/
	@echo "✅ TLS certificates generated and deployed to Trust EC2!"
.PHONY: regen-trust-certs
regen-trust-certs: ## Re-generate TLS certs for Trust EC2, deploy to EC2, and update Secrets Manager
	@echo "🔑 Regenerating TLS certificates for Trust EC2..."
	@TRUST_HOST="$$(terraform output -raw TrustEc2PublicIp)" bash ../../../trust/certs/generate-trust-certs.sh
	@echo "📋 Copying new CA cert to provider directory..."
	@cp ../../../trust/certs/trust-ca.crt trust-ca.crt
	@echo "📦 Copying certificates to Trust EC2..."
	@scp -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null \
		../../../trust/certs/trust-server.crt \
		../../../trust/certs/trust-server.key \
		../../../trust/certs/trust-ca.crt \
		ubuntu@$$(terraform output -raw TrustEc2PublicIp):/opt/flip/certs/
	@echo "🔄 Restarting nginx-tls on Trust EC2 to load new certificate..."
	@ssh flip-trust "docker restart trust1-nginx-tls-1"
	@echo "🔐 Updating Secrets Manager..."
	@$(MAKE) update-trust-secret
	@echo "✅ Certificate rotation complete!"

.PHONY: apply
apply: ## Apply Terraform configuration to update AWS infrastructure (with environment file loaded)
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
	@test -n "$(FL_KIT_DATE)" || (echo "FL_KIT_DATE must be set in $(MAIN_ENV_FILE)"; exit 1)
	@uv sync
	@uv run ansible-galaxy install -r requirements.yml
	@uv run ansible-playbook site.yml -e flip_aicentre_bucket=$(AICENTRE_BUCKET_NAME) -e fl_kit_date=$(FL_KIT_DATE)

.PHONY: write-trust-ca
write-trust-ca: ## Fetch trust_ca_cert from Secrets Manager and write to CH EC2 host for volume mount
	@echo "🔐 Fetching Trust CA cert from Secrets Manager and writing to CH EC2..."
	@CH_IP=$$(terraform output -raw Ec2PublicIp) && \
	  echo "   Waiting for SSH to be available on $$CH_IP..." && \
	  for i in $$(seq 1 120); do \
	    ssh -i ~/.ssh/host-aws -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null ubuntu@$$CH_IP "exit 0" 2>/dev/null && echo "   ✓ SSH ready (attempt $$i)" && break; \
	    if [ $$i -eq 120 ]; then echo "   ✗ SSH did not become available after 10 minutes."; exit 1; fi; \
	    echo "   ... waiting (attempt $$i/120)"; \
	    sleep 5; \
	  done && \
	  CA_CERT=$$(AWS_PROFILE=$(AWS_PROFILE) aws secretsmanager get-secret-value \
	    --secret-id $(AWS_SECRET_NAME) \
	    --query 'SecretString' --output text | python3 -c \
	    "import json,sys; s=json.load(sys.stdin); print(s['trust_ca_cert'])") && \
	  ssh -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null ubuntu@$$CH_IP "mkdir -p /opt/flip/certs && printf '%s\n' '$$CA_CERT' > /opt/flip/certs/trust-ca.crt && chmod 0644 /opt/flip/certs/trust-ca.crt" && \
	  echo "✅ Trust CA cert written to CH EC2 at /opt/flip/certs/trust-ca.crt"

.PHONY: deploy-centralhub
deploy-centralhub: write-trust-ca ## Deploy FLIP containers to flip context with environment from Terraform
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
	@echo "🔑 Refreshing SSH host key for CH EC2 (ensures Docker context SSH works after re-provisioning)..."
	@CH_IP=$$(terraform output -raw Ec2PublicIp) && \
	  ssh-keygen -R "$$CH_IP" 2>/dev/null || true && \
	  ssh-keyscan -H "$$CH_IP" >> ~/.ssh/known_hosts
	@echo "👷 Create docker context if not exists, pointing to flip to ssh://flip"
	@docker context create flip --docker "host=ssh://flip" || echo "   Context 'flip' already exists"
	@# Switch to flip context
	@echo "🔄 Switching to flip Docker context..."
	@docker context use flip
	@echo "🐳 Initialising Docker Swarm on CH EC2 (required for overlay networks)..."
	@docker swarm init || echo "   Swarm already initialized"
	@echo "🐳 Deploying containers..."
	@$(MAKE) -C ../../../ up-centralhub-ec2 PROD=${PROD}
	@echo "✅ Deployment complete!"
	@docker context use default

# Default SSH user for on-premises trust hosts (override with LOCAL_TRUST_SSH_USER=<user>)
LOCAL_TRUST_SSH_USER ?= ubuntu
# Trust kit number for the local (on-prem) trust FL client (default 2; EC2 trust uses 1)
LOCAL_TRUST_NUMBER ?= 2

.PHONY: deploy-trust
deploy-trust: update-env ## Deploy trust relationships after central hub is deployed
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
	@echo "⚙️  Configure .ssh/config for flip context..."
	@set -x AWS_PROFILE ${AWS_PROFILE}
	@terraform output
	@python3 update_ssh_config.py --aws-profile ${AWS_PROFILE} || true
	@echo "✅ .ssh/config updated!"
	@echo "🔑 Refreshing SSH host key for Trust EC2 (ensures Docker context SSH works after re-provisioning)..."
	@TRUST_IP=$$(terraform output -raw TrustEc2PublicIp) && \
	  ssh-keygen -R "$$TRUST_IP" 2>/dev/null || true && \
	  ssh-keyscan -H "$$TRUST_IP" >> ~/.ssh/known_hosts
	@echo "👷 Create docker context if not exists, pointing to flip-trust to ssh://flip-trust"
	@docker context create flip-trust --docker "host=ssh://flip-trust" || echo "   Context 'flip-trust' already exists"
	@echo "🔄 Switching to flip-trust Docker context..."
	@docker context use flip-trust
	@echo "🐳 Deploying containers to Trust EC2 ..."
	docker swarm init || echo "   Swarm already initialized"
	@echo "🔧 Creating Docker networks on Trust EC2..."
	$(MAKE) -C ../../../ create-networks
	@echo "🗒️ Copying TLS certificates to Trust EC2..."
	ssh -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null ubuntu@$$(terraform output -raw TrustEc2PublicIp) "mkdir -p /opt/flip/certs"
	scp -i ~/.ssh/host-aws -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null \
		../../../trust/certs/trust-server.crt \
		../../../trust/certs/trust-server.key \
		../../../trust/certs/trust-ca.crt \
		ubuntu@$$(terraform output -raw TrustEc2PublicIp):/opt/flip/certs/
	@echo "✅ Certificates copied!"
	@echo "🚀 Deploying Trust services..."
	$(MAKE) -C ../../../ up-trust-ec2 PROD=${PROD}
	@echo "✅ Trust deployment complete!"

.PHONY: add-local-trust
add-local-trust: ## Provision an on-prem Trust host and register it with the Central Hub
	## LOCAL_TRUST_SSH_KEY is optional. When omitted, runs locally (no SSH) — use this
	## when the trust host IS the operator workstation or is not reachable via its public IP.
	## Local mode generates TLS certs and prints the sudo commands to run (UFW, cert install, kit deploy).
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
			-e ch_public_ip="$$CH_IP" \
			-e trust_api_port=$(TRUST_API_PORT); \
	else \
		echo "💻 Running locally (no SSH — operator machine is the trust host)..."; \
		echo ""; \
		echo "🔑 Generating local trust TLS certificates..."; \
		LOCAL_CERT_DIR=/tmp/flip-local-trust-certs; \
		mkdir -p $$LOCAL_CERT_DIR; \
		TRUST_HOST=$(LOCAL_TRUST_IP) OUT_DIR=$$LOCAL_CERT_DIR bash ../../../trust/certs/generate-trust-certs.sh || exit 1; \
		cp $$LOCAL_CERT_DIR/trust-ca.crt ../../../trust/certs/local-trust-ca.crt; \
		echo ""; \
		echo "✅ Local trust CA saved to trust/certs/local-trust-ca.crt"; \
		echo ""; \
		echo "⚠️  Run the following commands on this machine to complete Trust host setup:"; \
		echo ""; \
		echo "  # 1. Install TLS certificates (required by the Trust nginx-tls container):"; \
		echo "  sudo mkdir -p /opt/flip/certs"; \
		echo "  sudo cp $$LOCAL_CERT_DIR/trust-ca.crt /opt/flip/certs/trust-ca.crt"; \
		echo "  sudo cp $$LOCAL_CERT_DIR/trust-server.crt /opt/flip/certs/trust-server.crt"; \
		echo "  sudo cp $$LOCAL_CERT_DIR/trust-server.key /opt/flip/certs/trust-server.key"; \
		echo ""; \
		echo "  # 2. Configure firewall — allow Central Hub ($$CH_IP) access to Trust ports:"; \
		echo "  sudo ufw allow from $$CH_IP to any port $(TRUST_API_PORT) proto tcp comment 'FLIP Trust-API HTTPS from AWS CH'"; \
		echo "  sudo ufw allow from $$CH_IP to any port 8002 proto tcp comment 'FL Server port from AWS CH'"; \
		echo "  sudo ufw allow from $$CH_IP to any port 8003 proto tcp comment 'FL Admin port from AWS CH'"; \
		echo "  sudo ufw default deny incoming"; \
		echo "  sudo ufw default allow outgoing"; \
		echo "  sudo ufw enable"; \
	fi
	@echo "📦 Downloading Trust_$(LOCAL_TRUST_NUMBER) FL participant kit from S3..."
	@LOCAL_TRUST_KIT_DIR=/tmp/Trust_$(LOCAL_TRUST_NUMBER)_kit && \
	  rm -rf $$LOCAL_TRUST_KIT_DIR && mkdir -p $$LOCAL_TRUST_KIT_DIR && \
	  AWS_PROFILE=$(AWS_PROFILE) aws s3 sync \
	    s3://$(AICENTRE_BUCKET_NAME)/fl-flare-participant-kits/$(FL_KIT_DATE)/net-1/services/Trust_$(LOCAL_TRUST_NUMBER)/ \
	    $$LOCAL_TRUST_KIT_DIR && \
	  if [ -n "$(LOCAL_TRUST_SSH_KEY)" ]; then \
	    ssh -i $(LOCAL_TRUST_SSH_KEY) $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP) \
	      "mkdir -p /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/startup /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/local /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/transfer" && \
	    rsync -av --delete -e "ssh -i $(LOCAL_TRUST_SSH_KEY)" \
	      $$LOCAL_TRUST_KIT_DIR/startup/ $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP):/opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/startup/ && \
	    rsync -av --delete -e "ssh -i $(LOCAL_TRUST_SSH_KEY)" \
	      $$LOCAL_TRUST_KIT_DIR/local/ $(LOCAL_TRUST_SSH_USER)@$(LOCAL_TRUST_IP):/opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/local/; \
	  else \
	    echo ""; \
	    echo "  # 3. Deploy the Trust_$(LOCAL_TRUST_NUMBER) FL participant kit (kit staged at $$LOCAL_TRUST_KIT_DIR):"; \
	    echo "  sudo mkdir -p /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/startup /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/local /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/transfer"; \
	    echo "  sudo rsync -av --delete $$LOCAL_TRUST_KIT_DIR/startup/ /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/startup/"; \
	    echo "  sudo rsync -av --delete $$LOCAL_TRUST_KIT_DIR/local/ /opt/flip/services/Trust_$(LOCAL_TRUST_NUMBER)/local/"; \
	  fi && \
	  echo "✅ Trust_$(LOCAL_TRUST_NUMBER) FL kit staged at $$LOCAL_TRUST_KIT_DIR"
	@echo "📋 Building CA bundle (EC2 trust CA + local trust CA) for Secrets Manager..."
	@# trust-ca.crt is the cloud Trust EC2 CA (written by gen-trust-ec2-certs).
	@# local-trust-ca.crt is the on-prem trust CA (generated by generate-trust-certs.sh above).
	@# Bundle them so flip-api can verify HTTPS connections to both trusts.
	@if [ -f ./trust-ca.crt ]; then \
		cat ./trust-ca.crt ../../../trust/certs/local-trust-ca.crt > /tmp/trust-ca-bundle.crt && \
		cp /tmp/trust-ca-bundle.crt ./trust-ca.crt && \
		rm /tmp/trust-ca-bundle.crt && \
		echo "   Bundled EC2 CA + local CA → trust-ca.crt"; \
	else \
		cp ../../../trust/certs/local-trust-ca.crt ./trust-ca.crt && \
		echo "   No EC2 CA found — using local CA only"; \
	fi
	@echo "🔐 Updating AWS security group rules and Secrets Manager CA cert..."
	@TF_VAR_local_trust_public_ip=$(LOCAL_TRUST_IP) terraform plan \
		-target=aws_security_group_rule.local_trust_fl_server \
		-target=aws_security_group_rule.local_trust_fl_admin \
		-target='module.flip_api_secret.aws_secretsmanager_secret_version.this[0]' \
		-out=local-trust.tfplan
	@TF_VAR_local_trust_public_ip=$(LOCAL_TRUST_IP) terraform apply local-trust.tfplan
	@rm -f local-trust.tfplan
	@echo ""
	@echo "✅ On-premises Trust host provisioned!"
	@echo ""
	@echo "⚠️  REMAINING MANUAL STEPS:"
	@echo "   1. Configure border router NAT: forward $(TRUST_API_PORT)/tcp → <trust-host-lan-ip>:$(TRUST_API_PORT)"
	@echo "   2. Update Trust endpoint in Secrets Manager (recommended helper target):"
	@echo "      make set-local-trust-endpoint LOCAL_TRUST_IP=$(LOCAL_TRUST_IP)"
	@echo "   3. On the trust host — start the trust stack: cd trust && PROD=$(PROD) make up-local-trust-stag"
	@echo "   4. Verify with CA validation: make test-local-trust LOCAL_TRUST_IP=$(LOCAL_TRUST_IP)"
	@echo "      (or handshake-only test: make test-local-trust-insecure LOCAL_TRUST_IP=$(LOCAL_TRUST_IP))"

.PHONY: set-local-trust-endpoint
set-local-trust-endpoint: ## Update FLIP_API Trust_1-endpoint to the local trust HTTPS URL
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make set-local-trust-endpoint LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
	@bash scripts/manage-secrets.sh set-local-trust-endpoint $(LOCAL_TRUST_IP)

.PHONY: test-local-trust
test-local-trust: ## Verify HTTPS connectivity to an on-premises Trust host
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make test-local-trust LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
	@echo "🔍 Testing HTTPS connection: https://$(LOCAL_TRUST_IP):$(TRUST_API_PORT)/health"
	@curl -sf --cacert ../../../trust/certs/local-trust-ca.crt \
		https://$(LOCAL_TRUST_IP):$(TRUST_API_PORT)/health \
	&& echo "✅ HTTPS connection to local Trust host successful!" \
	|| (echo "❌ Connection failed. Check:"; \
	    echo "   - Trust stack is running on the host: PROD=$(PROD) make up-local-trust-stag"; \
	    echo "   - UFW allows $(TRUST_API_PORT) from this machine (run: make add-local-trust)"; \
	    echo "   - Border router forwards $(TRUST_API_PORT)/tcp to the trust host LAN IP"; \
	    exit 1)

.PHONY: test-local-trust-insecure
test-local-trust-insecure: ## Verify HTTPS handshake to local trust without CA validation (-k)
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make test-local-trust-insecure LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
	@echo "🔍 Testing HTTPS handshake without CA validation: https://$(LOCAL_TRUST_IP):$(TRUST_API_PORT)/health"
	@curl -skf https://$(LOCAL_TRUST_IP):$(TRUST_API_PORT)/health \
	&& echo "✅ HTTPS handshake successful (insecure mode)." \
	|| (echo "❌ Connection failed. Check network/NAT/UFW and service health."; exit 1)

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


.PHONY: backup-eips
backup-eips: ## Backup current Elastic IPs before destruction (preserves allocation IDs for reassociation)
	@bash scripts/backup-eips.sh

.PHONY: destroy
destroy: backup-eips ## Destroy ephemeral infrastructure (EC2, RDS, VPC) - preserves Cognito, Secrets, S3, EIPs
	@echo ""
	@echo "⚠️  SELECTIVE DESTRUCTION - PRESERVING:"
	@echo "   ✓ Cognito user pool and identities"
	@echo "   ✓ AWS Secrets Manager (FLIP_API secret)"
	@echo "   ✓ S3 buckets (FLIP + AI Centre data)"
	@echo "   ✓ ACM certificates and Route53 records"
	@echo "   ✓ Elastic IPs (for Central Hub and Trust EC2)"
	@echo ""
	@echo "🗑️  DESTROYING:"
	@echo "   • EC2 instances (flip, trust) - instances only, EIPs preserved"
	@echo "   • RDS database (PostgreSQL)"
	@echo "   • VPC and security groups"
	@echo "   • ALB/NLB load balancers"
	@echo "   • IAM roles and policies"
	@echo "   • CloudWatch logs"
	@echo ""
	@read -p "Type 'yes' to confirm destruction: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted."; exit 1)
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

.PHONY: full-deploy
full-deploy: ## Full AWS deployment: provision infrastructure, generate TLS certs, deploy cloud Trust + Central Hub
	## Step order:
	## 1. login + init + import: auth, Terraform init, import any pre-existing resources
	## 2. First plan+apply: provision all AWS infrastructure (EC2, RDS, VPC, secrets – CA cert placeholder)
	## 3. update-env: refresh .env with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 4. gen-trust-ec2-certs: generate TLS certs using the now-known Trust EC2 IP; copy CA cert to
	##    Terraform dir so the next apply can embed it into the Secrets Manager secret
	## 5. Second plan+apply: update Secrets Manager with real CA cert
	## 6. ssh-config + ansible-init: configure SSH aliases and provision EC2s with Docker
	## 7. deploy-centralhub + deploy-trust + status: deploy containers and verify
full-deploy: github-login aws-login init import-persistent plan apply update-env gen-trust-ec2-certs plan apply ssh-config ansible-init deploy-centralhub deploy-trust status

.PHONY: full-deploy-stag
full-deploy-stag: ## Full staging deployment with on-prem local trust (requires LOCAL_TRUST_IP=<public-ip>)
	## Step order:
	## 1. login + init + import: auth, Terraform init, import pre-existing persistent resources
	## 2. First plan+apply (PROD=stag): provision all AWS infrastructure (EC2, RDS, VPC, ALB, secrets)
	## 3. update-env: refresh .env.stag with Terraform outputs (EC2 IPs, DB endpoint, Cognito IDs)
	## 4. gen-trust-ec2-certs: generate TLS certs for cloud Trust EC2; copy CA cert to Terraform dir
	## 5. Second plan+apply (PROD=stag): update Secrets Manager with real EC2 trust CA cert
	## 6. ssh-config + ansible-init: configure SSH aliases, provision EC2s with Docker + FL kit
	## 7. deploy-centralhub + deploy-trust: deploy Central Hub and cloud Trust containers
	## 8. add-local-trust LOCAL_TRUST_IP=<ip>: generate local trust TLS certs, configure UFW,
	##    deploy local Trust_$(LOCAL_TRUST_NUMBER) FL kit, update CA bundle in Secrets Manager,
	##    and update AWS security groups
	## 9. status: verify all services healthy
	## NOTE: After this completes you must manually:
	##   - Update Trust endpoint in FLIP_API secret: make set-local-trust-endpoint LOCAL_TRUST_IP=<public-ip>
	##   - Start local trust stack: cd ../../../trust && env PROD=stag make up-local-trust-stag
	##   - Restart Central Hub API: make deploy-centralhub PROD=stag
	@[ -n "$(LOCAL_TRUST_IP)" ] || (echo ""; echo "❌  ERROR: LOCAL_TRUST_IP is required."; echo "   Usage: make full-deploy-stag LOCAL_TRUST_IP=<public-ip>"; echo ""; exit 1)
full-deploy-stag: github-login aws-login init import-persistent plan apply update-env gen-trust-ec2-certs plan apply ssh-config ansible-init deploy-centralhub deploy-trust add-local-trust status

.PHONY: full-deploy-stag-hybrid
full-deploy-stag-hybrid: ## Full staging hybrid deployment including endpoint update and CH redeploy
	## This wraps full-deploy-stag, then updates Trust_1-endpoint and redeploys Central Hub.
	## You still need to start the local trust stack on the trust host.
	## If LOCAL_TRUST_IP is not provided, it will be auto-detected using curl.
	@if [ -z "$(LOCAL_TRUST_IP)" ]; then \
		echo "🔍 Detecting public IP for LOCAL_TRUST_IP..."; \
		LOCAL_TRUST_IP=$$(curl -s https://api.ipify.org) && \
		echo "✅ Detected LOCAL_TRUST_IP: $$LOCAL_TRUST_IP" || (echo "❌  Failed to detect IP. Provide LOCAL_TRUST_IP manually."; echo "   Usage: make full-deploy-stag-hybrid LOCAL_TRUST_IP=<public-ip>"; exit 1); \
	else \
		LOCAL_TRUST_IP="$(LOCAL_TRUST_IP)"; \
	fi && \
	$(MAKE) full-deploy-stag LOCAL_TRUST_IP=$$LOCAL_TRUST_IP LOCAL_TRUST_SSH_KEY="$(LOCAL_TRUST_SSH_KEY)" LOCAL_TRUST_SSH_USER="$(LOCAL_TRUST_SSH_USER)" LOCAL_TRUST_NUMBER="$(LOCAL_TRUST_NUMBER)" && \
	$(MAKE) set-local-trust-endpoint LOCAL_TRUST_IP=$$LOCAL_TRUST_IP && \
	$(MAKE) deploy-centralhub PROD=stag && \
	echo "" && \
	echo "✅ Hybrid staging deployment complete." && \
	echo "➡️  Final manual step: start the local trust stack on the trust host:" && \
	echo "   cd ../../../trust && env PROD=stag make up-local-trust-stag" && \
	echo "➡️  Then verify: make test-local-trust LOCAL_TRUST_IP=$$LOCAL_TRUST_IP"
