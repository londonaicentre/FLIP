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
# The Azure trust node on a VM (FLIP#1390): run from this directory, or with
# make -C trust/deploy/terraform/azure/vm. bootstrap, test and lint are in the directory above.

include ../common.mk

FL_BACKEND ?= nvflare
SSH_PUBLIC_KEY_FILE ?= $(HOME)/.ssh/id_ed25519.pub
FLIP_REF ?= $(shell git rev-parse HEAD)
UNIT ?= flip-provision
LINES ?= 80

# Azure needs an SSH key only to create the VM, so a teardown needs no key file. The provider parses
# it even on destroy, so it is a real public key whose private half was discarded.
DESTROY_SSH_PUBLIC_KEY := ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINNJS9sQraFNfnq0au6+vksilL+ct4B2O0yRqzcYY+oe destroy-only

# Run Command: the node's only way in. flip-node does the work; long steps run as units.
define run_on_node
	az vm run-command invoke --subscription "$(SUB_ID)" -g $(NODE_RG) -n $(NODE_VM) \
		--command-id RunShellScript --scripts "$(1)" --query 'value[0].message' -o tsv
endef

# Run Command hands its script to a shell on the node, so every argument is checked here, before it
# goes into that script: flip-node's own checks run only after the node's shell has parsed it. The
# patterns are flip-node's.
REF_PATTERN := ([0-9a-f]{40}|v[0-9]+\.[0-9]+\.[0-9]+)
BACKEND_PATTERN := (nvflare|flower)
BLOB_PATTERN := [A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz
HUB_PATTERN := (stag|prod)
UNIT_PATTERN := [A-Za-z0-9][A-Za-z0-9@._-]*
LINES_PATTERN := [0-9]{1,5}

.PHONY: help init plan apply destroy status reprovision selftest kit-upload selftest-kit join logs report stop start

help: ## Show this help message
	$(call list_targets,FLIP Azure trust node on a VM (bootstrap, test and lint: make -C .. help):)

init: ## Point this root at the bootstrap's state storage
	$(require_subscription)
	$(TF) init -input=false -reconfigure $(BACKEND_CONFIG) -backend-config="key=vm.tfstate"

plan: ## Plan the node (FLIP_REF must be pushed to GitHub)
	$(require_subscription)
	@[ -f "$(SSH_PUBLIC_KEY_FILE)" ] || { echo "❌ SSH_PUBLIC_KEY_FILE=$(SSH_PUBLIC_KEY_FILE) not found."; exit 1; }
	@git branch -r --contains "$(FLIP_REF)" | grep -q . || { echo "❌ $(FLIP_REF) is not on any pushed branch; the node clones from GitHub."; exit 1; }
	$(TF) plan -out=vm.tfplan \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" \
		-var "admin_ssh_public_key=$$(cat $(SSH_PUBLIC_KEY_FILE))" \
		-var "flip_ref=$(FLIP_REF)" -var "fl_backend=$(FL_BACKEND)" -var "operator_ip=$(OPERATOR_IP)"

apply: ## Show the saved plan and the target subscription, then apply it only after a typed yes
	$(require_subscription)
	$(TF) show vm.tfplan
	@echo ""
	@echo "Target subscription: $$(az account show --subscription "$(SUB_ID)" --query '[name, id]' -o tsv | paste -sd ' ' -)"
	@read -r -p "Apply this plan? Type yes to continue: " answer; [ "$$answer" = "yes" ] || { echo "Not applied."; exit 1; }
	$(TF) apply vm.tfplan
	@ip="$$($(TF) output -raw outbound_ip)"; echo "Outbound IP: $${ip:-none, outbound follows the site subnet (SUBNET_ID)}"

destroy: ## Destroy the node and its network, keep bootstrap (asks first)
	$(require_subscription)
	$(TF) destroy \
		-var "subscription_id=$(SUB_ID)" -var "location=$(LOCATION)" -var "name_prefix=$(NAME_PREFIX)" \
		-var "existing_subnet_id=$(SUBNET_ID)" \
		-var "admin_ssh_public_key=$(DESTROY_SSH_PUBLIC_KEY)" -var "flip_ref=$(FLIP_REF)" \
		-var "operator_ip=$(DESTROY_OPERATOR_IP)"

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
	@url=$$($(TF) output -raw kit_drop_url 2>/dev/null); \
	[ -n "$$url" ] || { echo "❌ No kit drop: apply the vm root with kit_drop_enabled=true first."; exit 1; }; \
	az storage blob upload --subscription "$(SUB_ID)" --auth-mode login --overwrite --only-show-errors --no-progress \
		--blob-url "$$url/$(BLOB_NAME)" --file "$(TARBALL)" -o none || { \
		echo "❌ Upload refused. A 403 usually means your IP ($(OPERATOR_IP)) is not the one the kit drop's firewall allows"; \
		echo "   (re-run make plan && make apply to update it), or your role assignment is still propagating."; exit 1; }; \
	echo "✅ Uploaded as $(BLOB_NAME); the blob is deleted within about 48 hours."

selftest-kit: ## Pack a self-test FL kit here and put it in the kit drop (FL_BACKEND=nvflare|flower)
	@$(call check_arg,FL_BACKEND,$(FL_BACKEND),$(BACKEND_PATTERN))
	bash ../scripts/pack-selftest-kit.sh $(FL_BACKEND) build/selftest-$(FL_BACKEND).tar.gz
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
