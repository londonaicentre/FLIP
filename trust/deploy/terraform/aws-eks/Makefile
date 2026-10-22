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
# A FLIP trust on Amazon EKS (FLIP#1439), the AKS root's twin. Every target needs the trust's kit
# file only (KIT=<CODE> HUB=<env>) and AWS credentials for the site's account; none reads a hub env
# file, hub state or hub credentials. apply and destroy show the plan and ask.
#
# Order: the hub admin registers the trust and packages its kit (make package-onprem-trust-kit), then
#   init plan apply credentials join TARBALL=<kit tarball>     first install
#   deploy                                                     redeploy from the installed kit

SHELL := /bin/bash
REPO_ROOT := $(abspath $(CURDIR)/../../../..)
include $(REPO_ROOT)/deploy/env_mode.mk

TF := terraform
KIT ?=
ifneq ($(KIT),)
ifneq ($(shell [[ '$(KIT)' =~ ^[A-Za-z0-9_]+$$ ]] && echo ok),ok)
$(error KIT='$(KIT)' is not a kit code: it must match [A-Za-z0-9_]+. Pass each variable as its own argument)
endif
endif
lower = $(shell printf '%s' '$(1)' | tr '[:upper:]_' '[:lower:]-')

NAME ?= $(if $(KIT),flip-$(ENV)-$(call lower,$(KIT)))
REGION ?= eu-west-2
STATE_BUCKET ?=
STATE_KEY ?= trust-nodes/$(NAME).tfstate
# Site subnets, comma-separated (two zones at least; the nodes go in the first). Empty creates a
# network with its own NAT gateway.
SUBNET_IDS ?=
INSTANCE_TYPE ?= m7i-flex.large
NODE_COUNT ?= 2
BOUNDARY_ARN ?=
# The public API endpoint admits only this address; looked up once per run.
OPERATOR_IP ?= $(eval OPERATOR_IP := $$(shell curl -fsS --max-time 10 https://api.ipify.org | grep -Ex '[0-9]{1,3}(\.[0-9]{1,3}){3}'))$(OPERATOR_IP)
ifneq ($(AWS_PROFILE),)
export AWS_PROFILE
endif

comma := ,
TF_VARS = -var 'name=$(NAME)' -var 'region=$(REGION)' \
	-var 'subnet_ids=[$(foreach s,$(subst $(comma), ,$(SUBNET_IDS)),"$(s)",)]' \
	-var 'instance_type=$(INSTANCE_TYPE)' -var 'node_count=$(NODE_COUNT)' \
	-var 'permissions_boundary_arn=$(BOUNDARY_ARN)'

define require_name
	@[ -n "$(NAME)" ] || { echo "❌ KIT=<CODE> HUB=<env> (or NAME=) is required: the cluster is named after the trust."; exit 1; }
endef

HELM_DIR := $(REPO_ROOT)/trust/deploy/helm
K8S_NAMESPACE ?= flip-trust
KUBECTL = kubectl --context $(NAME)
CHART = $(MAKE) -C $(HELM_DIR) KUBE_CONTEXT=$(NAME) NAMESPACE=$(K8S_NAMESPACE)
# Relative to the chart directory, where helm runs: the kit file records them.
EKS_VALUES ?= values-eks.yaml values-small-node.yaml$(if $(filter 1,$(MOCK_PACS)), ../terraform/azure/aks/values-mock-pacs.yaml)
KIT_DIR = build/kits/$(notdir $(TARBALL:.tar.gz=))
KIT_FILE = $(REPO_ROOT)/trust/.env.$(KIT).$(ENV)

.PHONY: help init plan apply destroy credentials storage-class join _fl-kit deploy undeploy selftest status test lint

help: ## Show this help message
	@echo "FLIP trust on Amazon EKS (KIT=<CODE> HUB=<env>):"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

init: ## Point the root at its state (STATE_BUCKET=, STATE_KEY= default trust-nodes/<NAME>.tfstate)
	@[ -n "$(STATE_BUCKET)" ] || { echo "❌ STATE_BUCKET=<S3 bucket for this cluster's state> is required."; exit 1; }
	$(require_name)
	$(TF) init -input=false -reconfigure -backend-config="bucket=$(STATE_BUCKET)" \
		-backend-config="key=$(STATE_KEY)" -backend-config="region=$(REGION)"

plan: ## Plan the cluster (its public API endpoint admits your IP only)
	$(require_name)
	@[ -n "$(OPERATOR_IP)" ] || { echo "❌ Cannot look up your public IP: pass OPERATOR_IP=<IPv4>."; exit 1; }
	$(TF) plan -out=eks.tfplan $(TF_VARS) -var 'operator_cidrs=["$(OPERATOR_IP)/32"]'

apply: ## Show the saved plan and the account, then apply it only after a typed yes
	$(TF) show eks.tfplan
	@echo ""
	@echo "Target account: $$(aws sts get-caller-identity --query Account --output text) ($(REGION))"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) apply eks.tfplan

# Destroy only deletes, so the API allowlist is never applied: a documentation address stands in.
destroy: ## Destroy the cluster and its network (asks first). Run make undeploy first
	$(require_name)
	@left="$$($(LEFT_VOLUMES))" || exit 1; [ -z "$$left" ] || { \
		echo "❌ The trust's EBS volumes still exist ($$left): make undeploy first, or they outlive the cluster."; exit 1; }
	$(TF) destroy $(TF_VARS) -var 'operator_cidrs=["192.0.2.1/32"]'

credentials: ## Write the kube context <NAME> (aws eks get-token, through your AWS profile)
	$(require_name)
	aws eks update-kubeconfig --region $(REGION) --name $(NAME) --alias $(NAME)

# XNAT's archive class (values-eks.yaml asks for it): an access point per claim on the cluster's EFS,
# owned by XNAT's uid, so xnat-web's init chowns its directories to the owner they already have.
storage-class: ## Create the xnat-efs storage class on the cluster's EFS file system
	$(require_name)
	@fs="$$($(TF) output -raw xnat_file_system_id)" && [ -n "$$fs" ] || { echo "❌ No xnat_file_system_id output: make init and apply first."; exit 1; }; \
	printf '%s\n' 'apiVersion: storage.k8s.io/v1' 'kind: StorageClass' 'metadata:' '  name: xnat-efs' \
	  'provisioner: efs.csi.aws.com' 'reclaimPolicy: Delete' 'parameters:' '  provisioningMode: efs-ap' \
	  "  fileSystemId: $$fs" '  directoryPerms: "755"' '  uid: "1001"' '  gid: "1001"' '  basePath: /xnat' \
	| $(KUBECTL) apply -f -

join: ## Install a hub-registered kit on the cluster and deploy the trust (TARBALL=<path> [MOCK_PACS=1])
	@[ -f "$(TARBALL)" ] && [ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ TARBALL=<kit tarball from the hub admin> KIT=<trust code> HUB=<env> are required."; exit 1; }
	@rm -rf "$(KIT_DIR)" && mkdir -p -m 700 "$(KIT_DIR)" && tar -xzf "$(TARBALL)" -C "$(KIT_DIR)" --no-same-owner
	@[ -f "$(KIT_DIR)/.env.$(KIT).$(ENV)" ] || { echo "❌ $(notdir $(TARBALL)) has no .env.$(KIT).$(ENV); is KIT the code it was packaged for?"; exit 1; }
	install -m 0600 "$(KIT_DIR)/.env.$(KIT).$(ENV)" "$(KIT_FILE)"
	@# Recorded in the kit file, so a plain upgrade-trust-k8s keeps the overlays too.
	printf 'K8S_PLATFORM_VALUES=%s\n' "$(EKS_VALUES)" >> "$(KIT_FILE)"
	python3 $(HELM_DIR)/scripts/generate_values.py --env-file "$(KIT_FILE)" \
		--output-dir "$(KIT_DIR)" --secrets-file $(HELM_DIR)/values-secrets.yaml
	$(KUBECTL) create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(MAKE) storage-class
	$(MAKE) _fl-kit KIT_DIR="$(KIT_DIR)" SLOT="$$(sed -n 's/^FL_KIT_SLOT=//p' "$(KIT_FILE)" | tail -1)"
	$(CHART) kit-secret KIT_SRC="$(abspath $(KIT_DIR))/fl-kit-secret"
	$(MAKE) deploy KIT=$(KIT) HUB=$(HUB)

# What goes into the FL kit Secret, as on AKS. NVFLARE: the slot's services/<SLOT>/. Flower: ca.crt
# and this slot's credential only, never the SuperLink's server.key beside them in the tarball.
_fl-kit:
	@[ -n "$(SLOT)" ] || { echo "❌ The kit file has no FL_KIT_SLOT; ask the hub admin to re-package the kit."; exit 1; }
	@k="$(KIT_DIR)/fl-kit/net-1"; s="$(KIT_DIR)/fl-kit-secret"; rm -rf "$$s"; \
	if [ -d "$$k/services/$(SLOT)" ]; then cp -Rp "$$k/services/$(SLOT)" "$$s"; \
	else mkdir -p -m 700 "$$s/certificates" "$$s/keys" && cp -p "$$k/certificates/ca.crt" "$$s/certificates/" && \
	  for f in "$$k"/keys/supernode_credentials_*; do case "$$f" in *.pub) ;; *) cp -p "$$f" "$$s/keys/" ;; esac; done; fi

# The overlays come from the kit file's K8S_PLATFORM_VALUES, which join recorded: passing EKS_VALUES
# here would drop any join was given (MOCK_PACS=1, an operator's own).
deploy: ## Deploy or upgrade the trust from its installed kit
	@[ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ KIT=<trust code> HUB=<env> are required."; exit 1; }
	$(CHART) deploy-trust-k8s KIT=$(KIT) HUB=$(HUB)

# The chart's volumes are EBS volumes the CSI driver made, not Terraform: deleting the namespace
# deletes their claims, and the default class's reclaim policy the volumes. A destroy without it
# leaves them behind, still billing.
undeploy: ## Remove the release and its namespace, and with it the trust's EBS volumes (the data goes)
	$(CHART) undeploy || true
	$(KUBECTL) delete namespace $(K8S_NAMESPACE) --ignore-not-found --wait

# EBS CSI tags each volume with the claim's namespace.
LEFT_VOLUMES = aws ec2 describe-volumes --region $(REGION) \
	--filters 'Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$(K8S_NAMESPACE)' \
	'Name=tag:ebs.csi.aws.com/cluster,Values=true' --query 'Volumes[].VolumeId' --output text

selftest: ## Run the trust self-test against the deployed release (report under build/selftest/)
	$(CHART) selftest

status: ## Release status and smoke checks
	$(CHART) status

# Tests get their own data dir: after make init, .terraform/ names the S3 backend and terraform test
# would want credentials.
test: ## Credential-free checks: terraform test, plus pytest
	TF_DATA_DIR=.terraform-test $(TF) init -backend=false -input=false >/dev/null
	TF_DATA_DIR=.terraform-test $(TF) validate -no-color
	TF_DATA_DIR=.terraform-test $(TF) test -no-color
	uv run --no-project --with pytest --with diagrams pytest tests/ -q

lint: ## terraform fmt check and tflint
	$(TF) fmt -check -recursive
	tflint --init --config "$(CURDIR)/.tflint.hcl" && tflint --config "$(CURDIR)/.tflint.hcl"
