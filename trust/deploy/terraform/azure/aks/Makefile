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
# The Azure trust node on a one-node AKS cluster, as the flip-trust chart (FLIP#1390): run from this
# directory, or with make -C trust/deploy/terraform/azure/aks. bootstrap, test and lint are in the
# directory above. The trial quota fits one 4-vCPU node: never run the cluster and the VM together.
# SUBNET_ID works as for the VM: the site's subnet and egress, or empty for a network with its own
# NAT. On a site subnet, NODE_EGRESS_IPS names the site's egress IPs, which the API server must admit.

include ../common.mk

# The AKS API server admits one IP, and an operator's public IP moves (home, office, VPN). This
# resets it without a plan. plan passes OPERATOR_IP to Terraform the same way, so its next plan
# shows no allowlist change as long as your IP has not moved again.
# The nodes' egress (the NAT gateway's IP, or NODE_EGRESS_IPS on a site subnet) stays admitted:
# without it the nodes lose their API server. Read from this root's state, and checked before use.
define admit_operator
	( nodes="$$($(TF) output -raw api_server_node_ranges)"; \
	  [[ "$$nodes" =~ ^$(IPV4_PATTERN)/32(,$(IPV4_PATTERN)/32)*$$ ]] || { \
		echo "❌ Cannot read the nodes' egress ranges from the aks state ('$$nodes'): not changing the API server's allowlist, which would lock the nodes out."; exit 1; }; \
	  az aks update --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME) --api-server-authorized-ip-ranges "$(OPERATOR_IP)/32,$$nodes" )
endef

# NODE_EGRESS_IPS (comma-separated) goes into a Terraform list, so it is checked first.
comma := ,
NODE_EGRESS_VAR = $(if $(NODE_EGRESS_IPS),-var 'node_egress_ips=["$(subst $(comma),"$(comma)",$(NODE_EGRESS_IPS))"]')
define require_node_egress_ips
	@$(if $(NODE_EGRESS_IPS),$(call check_arg,NODE_EGRESS_IPS,$(NODE_EGRESS_IPS),$(IPV4_PATTERN)(,$(IPV4_PATTERN))*),true)
endef

# The value is single-quoted for the local shell while it is checked, as check_arg does.
define require_operator_ip
	@v=$(call sq,$(OPERATOR_IP)); [[ "$$v" =~ ^$(IPV4_PATTERN)$$ ]] || { \
		echo "❌ OPERATOR_IP='$$v' is not one IPv4 address: the api.ipify.org lookup failed (curl's error, if any, is above) or answered something else."; \
		echo "   Pass yours: make $@ OPERATOR_IP=<your public IPv4>"; exit 1; }
endef

# The trust on the cluster: trust/deploy/helm's targets, on the kube context credentials wrote
# (named after the cluster). values-aks.yaml takes the FL kit from a Secret, values-small-node.yaml
# fits the trust on the one 4-vCPU node, and the vocabulary load is off: the site loads its own
# licensed copy (see the runbook). MOCK_PACS=1 seeds the mock studies into Orthanc, for a test node.
REPO_ROOT := $(abspath $(AZURE_DIR)/../../../..)
HELM_DIR := $(REPO_ROOT)/trust/deploy/helm
K8S_NAMESPACE ?= flip-trust
KUBECTL = kubectl --context $(AKS_NAME)
CHART = $(MAKE) -C $(HELM_DIR) KUBE_CONTEXT=$(AKS_NAME) NAMESPACE=$(K8S_NAMESPACE)
# Paths are relative to the chart directory, where helm runs: the kit file records them, so an
# absolute one would break an upgrade from a checkout somewhere else.
AKS_VALUES ?= values-aks.yaml values-small-node.yaml$(if $(filter 1,$(MOCK_PACS)), ../terraform/azure/aks/values-mock-pacs.yaml)
KIT_DIR = build/kits/$(notdir $(TARBALL:.tar.gz=))
# The kit file's name carries the env token (trust/.env.<KIT>.<env>), derived from HUB in one place.
ifneq ($(filter join,$(MAKECMDGOALS)),)
include $(REPO_ROOT)/deploy/env_mode.mk
endif

.PHONY: help init plan apply destroy credentials stop start allow-ip join _fl-kit deploy selftest clear-jobs

help: ## Show this help message
	$(call list_targets,FLIP Azure trust node on AKS (bootstrap, test and lint: make -C .. help):)

init: ## Point this root at the bootstrap's state storage
	$(require_subscription)
	$(TF) init -input=false -reconfigure $(BACKEND_CONFIG) -backend-config="key=aks.tfstate"

plan: ## Plan the AKS cluster (its API server admits your IP and the nodes' egress)
	$(require_node_egress_ips)
	$(require_subscription)
	$(TF) plan -out=aks.tfplan \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" $(NODE_EGRESS_VAR) -var "operator_ip=$(OPERATOR_IP)"

apply: ## Show the saved plan and the subscription, then apply it only after a typed yes
	$(require_subscription)
	@if [ "$$(az group exists --subscription "$(SUB_ID)" -n $(NODE_RG))" = "true" ]; then \
		echo "❌ $(NODE_RG) exists: the VM holds the trial's vCPUs. Destroy it first: make -C ../vm destroy"; exit 1; fi
	$(TF) show aks.tfplan
	@echo ""
	@echo "Target subscription: $$(az account show --subscription "$(SUB_ID)" --query '[name, id]' -o tsv | paste -sd ' ' -)"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) apply aks.tfplan
	@ip="$$($(TF) output -raw outbound_ip)"; echo "Outbound IP: $${ip:-none, outbound follows the site subnet (SUBNET_ID)}"

# The same placeholder stands in for the site's egress IPs: a teardown never applies the allowlist.
destroy: ## Destroy the AKS cluster and its network, keep bootstrap (asks first)
	$(require_subscription)
	$(TF) destroy \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" -var 'node_egress_ips=["$(DESTROY_OPERATOR_IP)"]' \
		-var "operator_ip=$(DESTROY_OPERATOR_IP)"

credentials: ## Fetch kubectl credentials (Entra sign-in through kubelogin)
	$(require_subscription)
	@command -v kubelogin >/dev/null || { echo "❌ kubelogin is required (the Azure CLI installs it with kubectl on any platform: aks install-cli)"; exit 1; }
	az aks get-credentials --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME) --overwrite-existing
	kubelogin convert-kubeconfig -l azurecli

stop: ## Stop the cluster (its node stops billing; disks and any NAT gateway stay)
	$(require_subscription)
	az aks stop --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME)

start: ## Start a stopped cluster, then admit your current IP (and the nodes' egress) to its API server
	$(require_operator_ip)
	$(require_subscription)
	az aks start --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME)
	$(admit_operator) || { \
		echo "❌ The cluster is running (and billing), but its API server still admits only its previous IP."; \
		echo "   Retry: make allow-ip AZ_SUBSCRIPTION=... Or stop it: make stop AZ_SUBSCRIPTION=..."; exit 1; }

allow-ip: ## Admit your current IP (and keep the nodes' egress) on the API server, without a plan (free)
	$(require_operator_ip)
	$(require_subscription)
	$(admit_operator)

join: ## Install a hub-registered kit on the cluster and deploy the trust (TARBALL=<path> KIT=<CODE> HUB=<env> [MOCK_PACS=1])
	@[ -f "$(TARBALL)" ] && [ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ TARBALL=<kit tarball from the hub admin> KIT=<trust code> HUB=<env> are required."; exit 1; }
	@$(call check_arg,KIT,$(KIT),$(KIT_PATTERN))
	@rm -rf "$(KIT_DIR)" && mkdir -p -m 700 "$(KIT_DIR)" && tar -xzf "$(TARBALL)" -C "$(KIT_DIR)" --no-same-owner
	@[ -f "$(KIT_DIR)/.env.$(KIT).$(ENV)" ] || { echo "❌ $(notdir $(TARBALL)) has no .env.$(KIT).$(ENV); is KIT the code it was packaged for?"; exit 1; }
	install -m 0600 "$(KIT_DIR)/.env.$(KIT).$(ENV)" "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)"
	@# The chart's own deploy and upgrade targets read the overlays from the kit file, so a plain
	@# upgrade-trust-k8s keeps them too (without values-aks.yaml PGDATA and the kit would move).
	printf 'K8S_PLATFORM_VALUES=%s\n' "$(AKS_VALUES)" >> "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)"
	python3 $(HELM_DIR)/scripts/generate_values.py --env-file "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)" \
		--output-dir "$(KIT_DIR)" --secrets-file $(HELM_DIR)/values-secrets.yaml
	$(KUBECTL) create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(MAKE) _fl-kit KIT_DIR="$(KIT_DIR)" SLOT="$$(sed -n 's/^FL_KIT_SLOT=//p' "$(KIT_DIR)/.env.$(KIT).$(ENV)" | head -1)"
	$(CHART) kit-secret KIT_SRC="$(abspath $(KIT_DIR))/fl-kit-secret"
	$(MAKE) deploy KIT=$(KIT) HUB=$(HUB)

# What goes into the FL kit Secret. NVFLARE: the slot's services/<SLOT>/ as shipped (SLOT is the kit's FL_KIT_SLOT). Flower: only what
# the SuperNode reads, ca.crt and this slot's credential; the tarball's certificates/ also holds the
# SuperLink's server.key, which kit-secret refuses to put in a trust's Secret.
_fl-kit:
	@[ -n "$(SLOT)" ] || { echo "❌ The kit file has no FL_KIT_SLOT; ask the hub admin to re-package the kit."; exit 1; }
	@k="$(KIT_DIR)/fl-kit/net-1"; s="$(KIT_DIR)/fl-kit-secret"; rm -rf "$$s"; \
	if [ -d "$$k/services/$(SLOT)" ]; then cp -Rp "$$k/services/$(SLOT)" "$$s"; \
	else mkdir -p -m 700 "$$s/certificates" "$$s/keys" && cp -p "$$k/certificates/ca.crt" "$$s/certificates/" && \
	  for f in "$$k"/keys/supernode_credentials_*; do case "$$f" in *.pub) ;; *) cp -p "$$f" "$$s/keys/" ;; esac; done; fi

deploy: ## Deploy or upgrade the trust on the cluster from its installed kit (KIT=<CODE> HUB=<env> [MOCK_PACS=1])
	@[ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ KIT=<trust code> HUB=<env> are required."; exit 1; }
	$(CHART) deploy-trust-k8s KIT=$(KIT) HUB=$(HUB) PLATFORM_VALUES="$(AKS_VALUES)"

selftest: ## Run the trust self-test against the deployed release (report under build/selftest/ at the repo root)
	$(CHART) selftest

# Container Service Jobs from before the conversion cap (FLIP#1448) still request a CPU each and can
# fill the node; a Job keeps the pod spec it was created with. The rule: a Job whose pod template
# carries a CPU limit at all. The capped registration has none (configure-dcm2niix.sh strips the
# resource keys on Kubernetes). TROUBLESHOOTING.md §2.3d.
clear-jobs: ## Delete dcm2niix Jobs created before the conversion cap (asks first)
	@set -o pipefail; jobs=$$($(KUBECTL) get jobs -n $(K8S_NAMESPACE) -l XNAT_PROJECT -o json | jq -r '.items[] \
		| select(.spec.template.spec.containers[0].resources.limits.cpu != null) | .metadata.name') || exit 1; \
	[ -n "$$jobs" ] || { echo "No Container Service Jobs from before the cap."; exit 0; }; \
	echo "$$jobs"; \
	echo "$$(echo "$$jobs" | wc -l | tr -d ' ') Jobs from before the cap. Their scans stay unconverted: relaunch dcm2niix from XNAT."; \
	read -r -p "Delete them? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Nothing deleted."; exit 1; }; \
	echo "$$jobs" | xargs $(KUBECTL) delete job -n $(K8S_NAMESPACE)
