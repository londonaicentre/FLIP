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
# Azure trust node (FLIP#1390): what the tree shares. The node has its own Makefile in vm/ and the
# cluster in aks/ (FLIP#1787), as the AWS roots do; both keep their state in the storage account
# bootstrap creates here.

include common.mk

BOOTSTRAP_LOCATION ?= uksouth
# The pin deploy/providers/AWS/scripts/checkov_lint.sh asserts; tests/test_static_guards.py keeps them equal.
CHECKOV_VERSION := 3.3.14
CHECKOV ?= uvx checkov==$(CHECKOV_VERSION)

.PHONY: help bootstrap destroy-bootstrap test lint checkov

help: ## Show this help message
	$(call list_targets,FLIP Azure trust node (the VM: make -C vm help; AKS: make -C aks help):)

bootstrap: ## One-off: state storage and budget alert (STATE_SA=<unique name> ALERT_EMAIL=<you>)
	$(require_subscription)
	@[ -n "$(STATE_SA)" ] || { echo "❌ STATE_SA=<3-24 lowercase letters/digits, globally unique> is required."; exit 1; }
	$(TF) -chdir=bootstrap init -input=false
	$(TF) -chdir=bootstrap apply \
		-var "subscription_id=$(SUB_ID)" -var "location=$(BOOTSTRAP_LOCATION)" \
		-var "name_prefix=$(NAME_PREFIX)" -var "state_storage_account_name=$(STATE_SA)" \
		-var "budget_start_date=$(shell date -u +%Y-%m-01T00:00:00Z)" \
		$(if $(ALERT_EMAIL),-var 'alert_emails=["$(ALERT_EMAIL)"]',-var create_budget=false)

destroy-bootstrap: ## Destroy everything bootstrap made (refuses while the node or the cluster exists; asks first)
	$(require_subscription)
	@for rg in $(NODE_RG) $(AKS_RG); do \
		if [ "$$(az group exists --subscription "$(SUB_ID)" -n $$rg)" = "true" ]; then \
		echo "❌ $$rg still exists. Run make destroy in vm/ or aks/ first: removing the state now would leave it spending credit with no state."; exit 1; fi; done
	$(TF) -chdir=bootstrap destroy -var "subscription_id=$(SUB_ID)" \
		-var "state_storage_account_name=$$($(BOOT) state_storage_account_name)" -var create_budget=false

test: ## Credential-free checks: terraform test in every root and module, plus pytest
	@for d in bootstrap modules/network modules/kit_drop vm aks; do \
		echo "── $$d"; ( cd $$d && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate -no-color && $(TF) test -no-color ) || exit 1; \
	done
	uv run --no-project --with pytest --with diagrams pytest tests/ -q

lint: ## terraform fmt check, tflint and checkov over the Azure tree
	$(TF) fmt -check -recursive
	tflint --init --config "$(CURDIR)/.tflint.hcl" && tflint --recursive --config "$(CURDIR)/.tflint.hcl"
	@$(MAKE) --no-print-directory checkov

# Every Azure check runs: deliberate posture is a `# checkov:skip=<ID>:<why>` in the resource, never
# a narrower check list. Checkov accepts a skip with no reason, so the grep refuses one first.
checkov: ## checkov's Azure checks over the tree (static, no credentials); a skip must say why
	@! grep -rEn '(#|//)[[:space:]]*checkov:skip=[A-Za-z0-9_]+[[:space:]]*(:[[:space:]]*)?$$' \
		--include='*.tf' --exclude-dir=.terraform . || { echo "❌ checkov:skip needs a reason: # checkov:skip=<ID>:<why>"; exit 1; }
	$(CHECKOV) -d . --framework terraform --skip-download --compact --quiet

# The node's and the cluster's targets moved to vm/ and aks/ in FLIP#1787. Their old names here
# say where, for one release, rather than doing nothing.
MOVED_TO_VM := init plan apply destroy status reprovision selftest kit-upload selftest-kit join logs report stop start
MOVED_TO_AKS := aks-init aks-plan aks-apply aks-destroy aks-credentials aks-stop aks-start aks-allow-ip aks-join aks-deploy \
	aks-selftest aks-clear-jobs
.PHONY: $(MOVED_TO_VM) $(MOVED_TO_AKS)
$(MOVED_TO_VM):
	@echo "❌ The node's targets moved to vm/: make -C trust/deploy/terraform/azure/vm $@"; exit 1
$(MOVED_TO_AKS):
	@echo "❌ The cluster's targets moved to aks/: make -C trust/deploy/terraform/azure/aks $(@:aks-%=%)"; exit 1
