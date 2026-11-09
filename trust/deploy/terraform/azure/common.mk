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
# What the Azure tree's three Makefiles share (FLIP#1390, split per root in FLIP#1787): this
# directory's (bootstrap, test, lint), vm/'s (the node) and aks/'s (the cluster). Every
# Azure-touching target needs AZ_SUBSCRIPTION (the trial subscription's name or id): the CLI default
# on the build account is a KCL subscription, so nothing here ever relies on it. No target skips
# Terraform's confirmation: apply, destroy and bootstrap all show the plan and ask.

SHELL := /bin/bash
TF := terraform
AZURE_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
NAME_PREFIX ?= flipaz
# The node's or the cluster's region; the bootstrap's state storage stays in BOOTSTRAP_LOCATION.
LOCATION ?= uksouth
# A subnet the site supplies (its resource id). Empty creates a network that leaves through its own
# NAT gateway; set, the node uses the site's routes and firewall and this tree creates no public IP.
SUBNET_ID ?=
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
BOOT = $(TF) -chdir=$(AZURE_DIR)/bootstrap output -raw
NODE_RG = $(NAME_PREFIX)-node-rg
NODE_VM = $(NAME_PREFIX)-node
AKS_RG = $(NAME_PREFIX)-aks-rg
AKS_NAME = $(NAME_PREFIX)-aks

# Both roots keep their state in the bootstrap's storage account, one key each: init adds the key.
BACKEND_CONFIG = -backend-config="subscription_id=$(SUB_ID)" \
	-backend-config="resource_group_name=$$($(BOOT) resource_group_name)" \
	-backend-config="storage_account_name=$$($(BOOT) state_storage_account_name)" \
	-backend-config="container_name=$$($(BOOT) state_container_name)" \
	-backend-config="use_azuread_auth=true"

# Destroy only deletes, so the kit drop's firewall and the API server's allowlist are never applied:
# a documentation address (TEST-NET-1) satisfies the variables' checks without looking the
# operator's IP up, so a blocked api.ipify.org never stands between the operator and a teardown.
DESTROY_OPERATOR_IP := 192.0.2.1

define require_subscription
	@[ -n "$(AZ_SUBSCRIPTION)" ] || { echo "❌ AZ_SUBSCRIPTION=<trial subscription name or id> is required (never the CLI default)."; exit 1; }
	@[[ "$(SUB_ID)" =~ ^$(GUID_PATTERN)$$ ]] || { echo "❌ Cannot read subscription '$(AZ_SUBSCRIPTION)'. Run: az login"; exit 1; }
endef

# A value is single-quoted for the local shell while it is checked against its pattern.
sq = '$(subst ','\'',$(1))'
KIT_PATTERN := [A-Za-z0-9_]+
# $(call check_arg,<variable name>,<value>,<pattern>)
check_arg = v=$(call sq,$(2)); [[ "$$v" =~ ^$(3)$$ ]] || { echo "❌ $(1)='$$v' is not valid: it must match $(3)."; exit 1; }

# $(call list_targets,<title>): the help text, from each target's ## comment.
define list_targets
	@echo "$(1)"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
endef
