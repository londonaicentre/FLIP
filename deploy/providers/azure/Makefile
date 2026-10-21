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
# Azure trust node (FLIP#1390). Every Azure-touching target needs AZ_SUBSCRIPTION (the trial
# subscription's name or id): the CLI default on the build account is a KCL subscription, so
# nothing here ever relies on it. No target skips Terraform's confirmation: apply, destroy and
# bootstrap all show the plan and ask.

SHELL := /bin/bash
TF := terraform
NAME_PREFIX ?= flipaz
# The node's region; the bootstrap's state storage stays in BOOTSTRAP_LOCATION.
LOCATION ?= uksouth
BOOTSTRAP_LOCATION ?= uksouth
FL_BACKEND ?= nvflare
# A subnet the site supplies (its resource id). Empty creates a network that leaves through its own
# NAT gateway; set, the node uses the site's routes and firewall and this tree creates no public IP.
SUBNET_ID ?=
SSH_PUBLIC_KEY_FILE ?= $(HOME)/.ssh/id_ed25519.pub
FLIP_REF ?= $(shell git rev-parse HEAD)
UNIT ?= flip-provision
LINES ?= 80
# Your public IP: the kit drop's firewall admits only it and the node subnet, and the AKS API
# server only it. Looked up lazily, once per make run, so every use in a run sees the same answer.
# Only an IPv4 address (every octet 0-255) is kept, because the answer reaches a shell; a failed
# lookup is empty, and curl's own error is left on the terminal.
OCTET_PATTERN := (25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])
IPV4_PATTERN := $(OCTET_PATTERN)(\.$(OCTET_PATTERN)){3}
OPERATOR_IP ?= $(eval OPERATOR_IP := $$(shell curl -fsS --max-time 10 https://api.ipify.org | grep -Ex '$(IPV4_PATTERN)' | head -n1))$(OPERATOR_IP)

# Resolved lazily, only by the targets that need Azure, and once per make run: az given an empty
# --subscription silently uses the CLI default, so every call must get the id that was checked.
SUB_ID = $(eval SUB_ID := $$(shell az account show --subscription "$(AZ_SUBSCRIPTION)" --query id -o tsv 2>/dev/null))$(SUB_ID)
GUID_PATTERN := [0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}
BOOT = $(TF) -chdir=bootstrap output -raw
NODE_RG = $(NAME_PREFIX)-node-rg
NODE_VM = $(NAME_PREFIX)-node
AKS_RG = $(NAME_PREFIX)-aks-rg
AKS_NAME = $(NAME_PREFIX)-aks

# Destroy only deletes, so the kit drop's firewall is never applied: a documentation address
# (TEST-NET-1) satisfies the variable's check without looking the operator's IP up, so a blocked
# api.ipify.org never stands between the operator and a teardown.
DESTROY_OPERATOR_IP := 192.0.2.1
# Likewise the SSH key: Azure needs one only to create the VM, so a teardown needs no key file. The
# provider parses it even on destroy, so it is a real public key whose private half was discarded.
DESTROY_SSH_PUBLIC_KEY := ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINNJS9sQraFNfnq0au6+vksilL+ct4B2O0yRqzcYY+oe destroy-only

define require_subscription
	@[ -n "$(AZ_SUBSCRIPTION)" ] || { echo "❌ AZ_SUBSCRIPTION=<trial subscription name or id> is required (never the CLI default)."; exit 1; }
	@[[ "$(SUB_ID)" =~ ^$(GUID_PATTERN)$$ ]] || { echo "❌ Cannot read subscription '$(AZ_SUBSCRIPTION)'. Run: az login"; exit 1; }
endef

# Run Command: the node's only way in. flip-node does the work; long steps run as units.
define run_on_node
	az vm run-command invoke --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM) \
		--command-id RunShellScript --scripts "$(1)" --query 'value[0].message' -o tsv
endef

# Run Command hands its script to a shell on the node, so every argument is checked here, before it
# goes into that script: flip-node's own checks run only after the node's shell has parsed it. The
# patterns are flip-node's; a value is single-quoted for the local shell while it is checked.
sq = '$(subst ','\'',$(1))'
REF_PATTERN := ([0-9a-f]{40}|v[0-9]+\.[0-9]+\.[0-9]+)
BACKEND_PATTERN := (nvflare|flower)
BLOB_PATTERN := [A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz
KIT_PATTERN := [A-Za-z0-9_]+
HUB_PATTERN := (stag|prod)
UNIT_PATTERN := [A-Za-z0-9][A-Za-z0-9@._-]*
LINES_PATTERN := [0-9]{1,5}
# $(call check_arg,<variable name>,<value>,<pattern>)
check_arg = v=$(call sq,$(2)); [[ "$$v" =~ ^$(3)$$ ]] || { echo "❌ $(1)='$$v' is not valid: it must match $(3)."; exit 1; }

# The AKS API server admits one IP, and an operator's public IP moves (home, office, VPN). This
# resets it without a plan. aks-plan passes OPERATOR_IP to Terraform the same way, so its next plan
# shows no allowlist change as long as your IP has not moved again.
# The nodes' egress (the NAT gateway's IP, or NODE_EGRESS_IPS on a site subnet) stays admitted:
# without it the nodes lose their API server. Read from the aks state, and checked before use.
define admit_operator
	( nodes="$$($(TF) -chdir=aks output -raw api_server_node_ranges)"; \
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

.PHONY: help bootstrap init plan apply destroy destroy-bootstrap status reprovision selftest kit-upload selftest-kit join aks-init aks-plan aks-apply aks-destroy aks-credentials aks-stop aks-start aks-allow-ip aks-join _aks-fl-kit aks-deploy aks-selftest aks-clear-jobs logs report stop start test lint

help: ## Show this help message
	@echo "FLIP Azure trust node commands:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-off: state storage and budget alert (STATE_SA=<unique name> ALERT_EMAIL=<you>)
	$(require_subscription)
	@[ -n "$(STATE_SA)" ] || { echo "❌ STATE_SA=<3-24 lowercase letters/digits, globally unique> is required."; exit 1; }
	$(TF) -chdir=bootstrap init -input=false
	$(TF) -chdir=bootstrap apply \
		-var "subscription_id=$(SUB_ID)" -var "location=$(BOOTSTRAP_LOCATION)" \
		-var "name_prefix=$(NAME_PREFIX)" -var "state_storage_account_name=$(STATE_SA)" \
		-var "budget_start_date=$(shell date -u +%Y-%m-01T00:00:00Z)" \
		$(if $(ALERT_EMAIL),-var 'alert_emails=["$(ALERT_EMAIL)"]',-var create_budget=false)

init: ## Point the vm root at the bootstrap's state storage
	$(require_subscription)
	$(TF) -chdir=vm init -input=false -reconfigure \
		-backend-config="subscription_id=$(SUB_ID)" \
		-backend-config="resource_group_name=$$($(BOOT) resource_group_name)" \
		-backend-config="storage_account_name=$$($(BOOT) state_storage_account_name)" \
		-backend-config="container_name=$$($(BOOT) state_container_name)" \
		-backend-config="key=vm.tfstate" \
		-backend-config="use_azuread_auth=true"

plan: ## Plan the node (FLIP_REF must be pushed to GitHub)
	$(require_subscription)
	@[ -f "$(SSH_PUBLIC_KEY_FILE)" ] || { echo "❌ SSH_PUBLIC_KEY_FILE=$(SSH_PUBLIC_KEY_FILE) not found."; exit 1; }
	@git branch -r --contains "$(FLIP_REF)" | grep -q . || { echo "❌ $(FLIP_REF) is not on any pushed branch; the node clones from GitHub."; exit 1; }
	$(TF) -chdir=vm plan -out=vm.tfplan \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" \
		-var "admin_ssh_public_key=$$(cat $(SSH_PUBLIC_KEY_FILE))" \
		-var "flip_ref=$(FLIP_REF)" -var "fl_backend=$(FL_BACKEND)" -var "operator_ip=$(OPERATOR_IP)"

apply: ## Show the saved plan and the target subscription, then apply it only after a typed yes
	$(require_subscription)
	$(TF) -chdir=vm show vm.tfplan
	@echo ""
	@echo "Target subscription: $$(az account show --subscription "$(SUB_ID)" --query '[name, id]' -o tsv | paste -sd ' ' -)"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) -chdir=vm apply vm.tfplan
	@ip="$$($(TF) -chdir=vm output -raw outbound_ip)"; echo "Outbound IP: $${ip:-none, outbound follows the site subnet (SUBNET_ID)}"

destroy: ## Destroy the node and its network, keep bootstrap (asks first)
	$(require_subscription)
	$(TF) -chdir=vm destroy \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" \
		-var "admin_ssh_public_key=$(DESTROY_SSH_PUBLIC_KEY)" -var "flip_ref=$(FLIP_REF)" \
		-var "operator_ip=$(DESTROY_OPERATOR_IP)"

destroy-bootstrap: ## Destroy everything bootstrap made (refuses while the node or the cluster exists; asks first)
	$(require_subscription)
	@for rg in $(NODE_RG) $(AKS_RG); do \
		if [ "$$(az group exists --subscription "$(SUB_ID)" -n $$rg)" = "true" ]; then \
		echo "❌ $$rg still exists. Run make destroy or make aks-destroy first: removing the state now would leave it spending credit with no state."; exit 1; fi; done
	$(TF) -chdir=bootstrap destroy -var "subscription_id=$(SUB_ID)" \
		-var "state_storage_account_name=$$($(BOOT) state_storage_account_name)" -var create_budget=false

status: ## First-boot and provisioning state of the node
	$(require_subscription)
	@$(call run_on_node,flip-node status)

reprovision: ## Re-run the play on the node (REF=<sha|vX.Y.Z> to move the code)
	@$(if $(REF),$(call check_arg,REF,$(REF),$(REF_PATTERN)),true)
	$(require_subscription)
	@$(call run_on_node,flip-node reprovision $(REF))

selftest: ## Start the hubless self-test on the node, on the kit selftest-kit delivered (FL_BACKEND=nvflare|flower)
	@$(call check_arg,FL_BACKEND,$(FL_BACKEND),$(BACKEND_PATTERN))
	$(require_subscription)
	@$(call run_on_node,flip-node selftest $(FL_BACKEND))

kit-upload: BLOB_NAME = $(or $(NAME),$(notdir $(TARBALL)))
kit-upload: ## Put a kit tarball in the node's kit drop (TARBALL=<path> [NAME=<blob name>]); deleted within about 48 hours
	@[ -f "$(TARBALL)" ] || { echo "❌ TARBALL=<path to a kit tarball> is required."; exit 1; }
	@$(call check_arg,NAME,$(BLOB_NAME),$(BLOB_PATTERN))
	$(require_subscription)
	@url=$$($(TF) -chdir=vm output -raw kit_drop_url 2>/dev/null); \
	[ -n "$$url" ] || { echo "❌ No kit drop: apply the vm root with kit_drop_enabled=true first."; exit 1; }; \
	az storage blob upload --subscription "$(SUB_ID)" --auth-mode login --overwrite --only-show-errors --no-progress \
		--blob-url "$$url/$(BLOB_NAME)" --file "$(TARBALL)" -o none || { \
		echo "❌ Upload refused. A 403 usually means your IP ($(OPERATOR_IP)) is not the one the kit drop's firewall allows"; \
		echo "   (re-run make plan && make apply to update it), or your role assignment is still propagating."; exit 1; }; \
	echo "✅ Uploaded as $(BLOB_NAME); the blob is deleted within about 48 hours."

selftest-kit: ## Pack a self-test FL kit here and put it in the kit drop (FL_BACKEND=nvflare|flower)
	@$(call check_arg,FL_BACKEND,$(FL_BACKEND),$(BACKEND_PATTERN))
	bash scripts/pack-selftest-kit.sh $(FL_BACKEND) build/selftest-$(FL_BACKEND).tar.gz
	$(MAKE) kit-upload TARBALL=build/selftest-$(FL_BACKEND).tar.gz NAME=selftest-$(FL_BACKEND).tar.gz
	@rm -f build/selftest-$(FL_BACKEND).tar.gz

join: ## Install a hub-registered kit on the node and bring the trust up (NAME=<blob> KIT=<code> HUB=stag)
	$(require_subscription)
	@[ -n "$(NAME)" ] && [ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ NAME=<uploaded kit blob> KIT=<trust code> HUB=<env> are required."; exit 1; }
	@$(call check_arg,NAME,$(NAME),$(BLOB_PATTERN))
	@$(call check_arg,KIT,$(KIT),$(KIT_PATTERN))
	@$(call check_arg,HUB,$(HUB),$(HUB_PATTERN))
	@$(call run_on_node,flip-node join $(NAME) $(KIT) $(HUB))

logs: ## Tail a node unit's log (UNIT=flip-selftest-nvflare LINES=80)
	@$(call check_arg,UNIT,$(UNIT),$(UNIT_PATTERN))
	@$(call check_arg,LINES,$(LINES),$(LINES_PATTERN))
	$(require_subscription)
	@$(call run_on_node,flip-node logs $(UNIT) $(LINES))

report: ## Print the latest self-test report (FL_BACKEND=nvflare|flower)
	@$(call check_arg,FL_BACKEND,$(FL_BACKEND),$(BACKEND_PATTERN))
	$(require_subscription)
	@$(call run_on_node,flip-node report $(FL_BACKEND))

stop: ## Deallocate the VM (compute stops billing; disks and any NAT gateway stay)
	$(require_subscription)
	az vm deallocate --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM)

start: ## Start a deallocated VM
	$(require_subscription)
	az vm start --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM)

# ── AKS: the same trust on a one-node managed cluster (the flip-trust chart) ─────────────────
# The trial quota fits one 4-vCPU node: never run the cluster and the VM together. SUBNET_ID
# works as for the VM: the site's subnet and egress, or empty for a network with its own NAT. On a
# site subnet, NODE_EGRESS_IPS names the site's egress IPs, which the API server must admit.

aks-init: ## Point the aks root at the bootstrap's state storage
	$(require_subscription)
	$(TF) -chdir=aks init -input=false -reconfigure \
		-backend-config="subscription_id=$(SUB_ID)" \
		-backend-config="resource_group_name=$$($(BOOT) resource_group_name)" \
		-backend-config="storage_account_name=$$($(BOOT) state_storage_account_name)" \
		-backend-config="container_name=$$($(BOOT) state_container_name)" \
		-backend-config="key=aks.tfstate" \
		-backend-config="use_azuread_auth=true"

aks-plan: ## Plan the AKS cluster (its API server admits your IP and the nodes' egress)
	$(require_node_egress_ips)
	$(require_subscription)
	$(TF) -chdir=aks plan -out=aks.tfplan \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" $(NODE_EGRESS_VAR) -var "operator_ip=$(OPERATOR_IP)"

aks-apply: ## Show the saved AKS plan and the subscription, then apply it only after a typed yes
	$(require_subscription)
	@if [ "$$(az group exists --subscription "$(SUB_ID)" -n $(NODE_RG))" = "true" ]; then \
		echo "❌ $(NODE_RG) exists: the VM holds the trial's vCPUs. make destroy first."; exit 1; fi
	$(TF) -chdir=aks show aks.tfplan
	@echo ""
	@echo "Target subscription: $$(az account show --subscription "$(SUB_ID)" --query '[name, id]' -o tsv | paste -sd ' ' -)"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) -chdir=aks apply aks.tfplan
	@ip="$$($(TF) -chdir=aks output -raw outbound_ip)"; echo "Outbound IP: $${ip:-none, outbound follows the site subnet (SUBNET_ID)}"

# The same placeholder stands in for the site's egress IPs: a teardown never applies the allowlist.
aks-destroy: ## Destroy the AKS cluster and its network, keep bootstrap (asks first)
	$(require_subscription)
	$(TF) -chdir=aks destroy \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" -var 'node_egress_ips=["$(DESTROY_OPERATOR_IP)"]' \
		-var "operator_ip=$(DESTROY_OPERATOR_IP)"

aks-credentials: ## Fetch kubectl credentials (Entra sign-in through kubelogin)
	$(require_subscription)
	@command -v kubelogin >/dev/null || { echo "❌ kubelogin is required (the Azure CLI installs it with kubectl on any platform: aks install-cli)"; exit 1; }
	az aks get-credentials --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME) --overwrite-existing
	kubelogin convert-kubeconfig -l azurecli

aks-stop: ## Stop the cluster (its node stops billing; disks and any NAT gateway stay)
	$(require_subscription)
	az aks stop --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME)

aks-start: ## Start a stopped cluster, then admit your current IP (and the nodes' egress) to its API server
	$(require_operator_ip)
	$(require_subscription)
	az aks start --subscription "$(SUB_ID)" -g $(AKS_RG) -n $(AKS_NAME)
	$(admit_operator) || { \
		echo "❌ The cluster is running (and billing), but its API server still admits only its previous IP."; \
		echo "   Retry: make aks-allow-ip AZ_SUBSCRIPTION=... Or stop it: make aks-stop AZ_SUBSCRIPTION=..."; exit 1; }

aks-allow-ip: ## Admit your current IP (and keep the nodes' egress) on the API server, without a plan (free)
	$(require_operator_ip)
	$(require_subscription)
	$(admit_operator)

# The trust on the cluster: trust/deploy/helm's targets, on the kube context aks-credentials wrote
# (named after the cluster). values-aks.yaml takes the FL kit from a Secret, values-small-node.yaml
# fits the trust on the one 4-vCPU node, and the vocabulary load is off: the site loads its own
# licensed copy (see the runbook). MOCK_PACS=1 seeds the mock studies into Orthanc, for a test node.
REPO_ROOT := $(abspath $(CURDIR)/../../..)
HELM_DIR := $(REPO_ROOT)/trust/deploy/helm
K8S_NAMESPACE ?= flip-trust
KUBECTL = kubectl --context $(AKS_NAME)
CHART = $(MAKE) -C $(HELM_DIR) KUBE_CONTEXT=$(AKS_NAME) NAMESPACE=$(K8S_NAMESPACE)
# Paths are relative to the chart directory, where helm runs: the kit file records them, so an
# absolute one would break an upgrade from a checkout somewhere else.
AKS_VALUES ?= values-aks.yaml values-small-node.yaml$(if $(filter 1,$(MOCK_PACS)), ../../../deploy/providers/azure/aks/values-mock-pacs.yaml)
AKS_KIT_DIR = build/kits/$(notdir $(TARBALL:.tar.gz=))
# The kit file's name carries the env token (trust/.env.<KIT>.<env>), derived from HUB in one place.
ifneq ($(filter aks-join,$(MAKECMDGOALS)),)
include $(REPO_ROOT)/deploy/env_mode.mk
endif

aks-join: ## Install a hub-registered kit on the cluster and deploy the trust (TARBALL=<path> KIT=<slot> HUB=<env> [MOCK_PACS=1])
	@[ -f "$(TARBALL)" ] && [ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ TARBALL=<kit tarball from the hub admin> KIT=<slot> HUB=<env> are required."; exit 1; }
	@$(call check_arg,KIT,$(KIT),$(KIT_PATTERN))
	@rm -rf "$(AKS_KIT_DIR)" && mkdir -p -m 700 "$(AKS_KIT_DIR)" && tar -xzf "$(TARBALL)" -C "$(AKS_KIT_DIR)" --no-same-owner
	@[ -f "$(AKS_KIT_DIR)/.env.$(KIT).$(ENV)" ] || { echo "❌ $(notdir $(TARBALL)) has no .env.$(KIT).$(ENV); is KIT the slot it was packaged for?"; exit 1; }
	install -m 0600 "$(AKS_KIT_DIR)/.env.$(KIT).$(ENV)" "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)"
	@# The chart's own deploy and upgrade targets read the overlays from the kit file, so a plain
	@# upgrade-trust-k8s keeps them too (without values-aks.yaml PGDATA and the kit would move).
	printf 'K8S_PLATFORM_VALUES=%s\n' "$(AKS_VALUES)" >> "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)"
	python3 $(HELM_DIR)/scripts/generate_values.py --env-file "$(REPO_ROOT)/trust/.env.$(KIT).$(ENV)" \
		--output-dir "$(AKS_KIT_DIR)" --secrets-file $(HELM_DIR)/values-secrets.yaml
	$(KUBECTL) create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(MAKE) _aks-fl-kit AKS_KIT_DIR="$(AKS_KIT_DIR)" KIT=$(KIT)
	$(CHART) kit-secret KIT_SRC="$(abspath $(AKS_KIT_DIR))/fl-kit-secret"
	$(MAKE) aks-deploy KIT=$(KIT) HUB=$(HUB)

# What goes into the FL kit Secret. NVFLARE: the slot's services/<slot>/ as shipped. Flower: only what
# the SuperNode reads, ca.crt and this slot's credential; the tarball's certificates/ also holds the
# SuperLink's server.key, which kit-secret refuses to put in a trust's Secret.
_aks-fl-kit:
	@k="$(AKS_KIT_DIR)/fl-kit/net-1"; s="$(AKS_KIT_DIR)/fl-kit-secret"; rm -rf "$$s"; \
	if [ -d "$$k/services/$(KIT)" ]; then cp -Rp "$$k/services/$(KIT)" "$$s"; \
	else mkdir -p -m 700 "$$s/certificates" "$$s/keys" && cp -p "$$k/certificates/ca.crt" "$$s/certificates/" && \
	  for f in "$$k"/keys/supernode_credentials_*; do case "$$f" in *.pub) ;; *) cp -p "$$f" "$$s/keys/" ;; esac; done; fi

aks-deploy: ## Deploy or upgrade the trust on the cluster from its installed kit (KIT=<slot> HUB=<env> [MOCK_PACS=1])
	@[ -n "$(KIT)" ] && [ -n "$(HUB)" ] || { echo "❌ KIT=<slot> HUB=<env> are required."; exit 1; }
	$(CHART) deploy-trust-k8s KIT=$(KIT) HUB=$(HUB) PLATFORM_VALUES="$(AKS_VALUES)"

aks-selftest: ## Run the trust self-test against the deployed release (report under build/selftest/ at the repo root)
	$(CHART) selftest

# Container Service Jobs from before the conversion cap (FLIP#1448) still request a CPU each and can
# fill the node; a Job keeps the pod spec it was created with. The rule: a Job whose pod template
# carries a CPU limit at all. The capped registration has none (configure-dcm2niix.sh strips the
# resource keys on Kubernetes). TROUBLESHOOTING.md §2.3d.
aks-clear-jobs: ## Delete dcm2niix Jobs created before the conversion cap (asks first)
	@set -o pipefail; jobs=$$($(KUBECTL) get jobs -n $(K8S_NAMESPACE) -l XNAT_PROJECT -o json | jq -r '.items[] \
		| select(.spec.template.spec.containers[0].resources.limits.cpu != null) | .metadata.name') || exit 1; \
	[ -n "$$jobs" ] || { echo "No Container Service Jobs from before the cap."; exit 0; }; \
	echo "$$jobs"; \
	echo "$$(echo "$$jobs" | wc -l | tr -d ' ') Jobs from before the cap. Their scans stay unconverted: relaunch dcm2niix from XNAT."; \
	read -r -p "Delete them? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Nothing deleted."; exit 1; }; \
	echo "$$jobs" | xargs $(KUBECTL) delete job -n $(K8S_NAMESPACE)

test: ## Credential-free checks: terraform test in every root and module, plus pytest
	@for d in bootstrap modules/network modules/kit_drop vm aks; do \
		echo "── $$d"; ( cd $$d && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate -no-color && $(TF) test -no-color ) || exit 1; \
	done
	uv run --no-project --with pytest pytest tests/ -q

lint: ## terraform fmt check and tflint over the Azure tree
	$(TF) fmt -check -recursive
	tflint --init --config "$(CURDIR)/.tflint.hcl" && tflint --recursive --config "$(CURDIR)/.tflint.hcl"
