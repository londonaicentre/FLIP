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
# The node's region. The outbound IP follows it; the bootstrap's state storage stays in
# BOOTSTRAP_LOCATION, so moving the node replaces only the IP.
LOCATION ?= uksouth
BOOTSTRAP_LOCATION ?= uksouth
FL_BACKEND ?= nvflare
SSH_PUBLIC_KEY_FILE ?= $(HOME)/.ssh/id_ed25519.pub
FLIP_REF ?= $(shell git rev-parse HEAD)
UNIT ?= flip-provision
LINES ?= 80

# Resolved lazily, only by the targets that need Azure.
SUB_ID = $(shell az account show --subscription "$(AZ_SUBSCRIPTION)" --query id -o tsv 2>/dev/null)
BOOT = $(TF) -chdir=bootstrap output -raw
NODE_RG = $(NAME_PREFIX)-node-rg
NODE_VM = $(NAME_PREFIX)-node

define require_subscription
	@[ -n "$(AZ_SUBSCRIPTION)" ] || { echo "❌ AZ_SUBSCRIPTION=<trial subscription name or id> is required (never the CLI default)."; exit 1; }
	@[ -n "$(SUB_ID)" ] || { echo "❌ Cannot read subscription '$(AZ_SUBSCRIPTION)'. Run: az login"; exit 1; }
endef

# Run Command: the node's only way in. flip-node does the work; long steps run as units.
define run_on_node
	az vm run-command invoke --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM) \
		--command-id RunShellScript --scripts "$(1)" --query 'value[0].message' -o tsv
endef

.PHONY: help bootstrap init plan apply destroy destroy-bootstrap status reprovision selftest logs report stop start test lint

help: ## Show this help message
	@echo "FLIP Azure trust node commands:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-off: state storage, budget alert, static outbound IP (STATE_SA=<unique name> ALERT_EMAIL=<you>)
	$(require_subscription)
	@[ -n "$(STATE_SA)" ] || { echo "❌ STATE_SA=<3-24 lowercase letters/digits, globally unique> is required."; exit 1; }
	$(TF) -chdir=bootstrap init -input=false
	$(TF) -chdir=bootstrap apply \
		-var "subscription_id=$(SUB_ID)" -var "location=$(BOOTSTRAP_LOCATION)" -var "egress_location=$(LOCATION)" \
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
		-var "egress_public_ip_id=$$($(BOOT) egress_public_ip_id)" \
		-var "admin_ssh_public_key=$$(cat $(SSH_PUBLIC_KEY_FILE))" \
		-var "flip_ref=$(FLIP_REF)" -var "fl_backend=$(FL_BACKEND)"

apply: ## Show the saved plan and the target subscription, then apply it only after a typed yes
	$(require_subscription)
	$(TF) -chdir=vm show vm.tfplan
	@echo ""
	@echo "Target subscription: $$(az account show --subscription "$(SUB_ID)" --query '[name, id]' -o tsv | paste -sd ' ' -)"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) -chdir=vm apply vm.tfplan
	@echo "Outbound IP: $$($(BOOT) egress_public_ip_address)"

destroy: ## Destroy the node, keep bootstrap and the outbound IP (asks first)
	$(require_subscription)
	$(TF) -chdir=vm destroy \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "egress_public_ip_id=$$($(BOOT) egress_public_ip_id)" \
		-var "admin_ssh_public_key=$$(cat $(SSH_PUBLIC_KEY_FILE))" -var "flip_ref=$(FLIP_REF)"

destroy-bootstrap: ## Destroy everything bootstrap made (refuses while the node exists; asks first)
	$(require_subscription)
	@if [ "$$(az group exists --subscription "$(SUB_ID)" -n $(NODE_RG))" = "true" ]; then \
		echo "❌ $(NODE_RG) still exists. Run make destroy first: removing the state now would leave a node spending credit with no state."; exit 1; fi
	$(TF) -chdir=bootstrap destroy -var "subscription_id=$(SUB_ID)" \
		-var "state_storage_account_name=$$($(BOOT) state_storage_account_name)" -var create_budget=false

status: ## First-boot and provisioning state of the node
	$(require_subscription)
	@$(call run_on_node,flip-node status)

reprovision: ## Re-run the play on the node (REF=<sha|vX.Y.Z> to move the code)
	$(require_subscription)
	@$(call run_on_node,flip-node reprovision $(REF))

selftest: ## Start the hubless self-test on the node (FL_BACKEND=nvflare|flower)
	$(require_subscription)
	@$(call run_on_node,flip-node selftest $(FL_BACKEND))

logs: ## Tail a node unit's log (UNIT=flip-selftest-nvflare LINES=80)
	$(require_subscription)
	@$(call run_on_node,flip-node logs $(UNIT) $(LINES))

report: ## Print the latest self-test report (FL_BACKEND=nvflare|flower)
	$(require_subscription)
	@$(call run_on_node,flip-node report $(FL_BACKEND))

stop: ## Deallocate the VM (compute stops billing; disks and IP stay)
	$(require_subscription)
	az vm deallocate --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM)

start: ## Start a deallocated VM
	$(require_subscription)
	az vm start --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM)

test: ## Credential-free checks: terraform test in every root and module, plus pytest
	@for d in bootstrap modules/network vm; do \
		echo "── $$d"; ( cd $$d && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate -no-color && $(TF) test -no-color ) || exit 1; \
	done
	uv run --no-project --with pytest pytest tests/ -q

lint: ## terraform fmt check and tflint over the Azure tree
	$(TF) fmt -check -recursive
	tflint --init --config "$(CURDIR)/.tflint.hcl" && tflint --recursive --config "$(CURDIR)/.tflint.hcl"
