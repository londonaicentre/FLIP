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

HELM := helm
NAMESPACE ?= flip-trust
RELEASE_NAME ?= trust-release
CHART_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
VALUES_FILE ?= $(CHART_DIR)/values.yaml

# Kit-file env token, derived from PROD the same way trust/Makefile does:
# PROD=true → production, PROD=stag → stag, otherwise development.
ifeq ($(PROD),true)
ENV := production
else ifeq ($(PROD),stag)
ENV := stag
else
ENV := development
endif
SECRETS_FILE := $(wildcard $(CHART_DIR)/values-secrets.yaml)
OVERRIDES_FILE ?=

# Determine which values files to pass
VALUES_OPTS := -f $(VALUES_FILE)
ifneq ($(strip $(SECRETS_FILE)),)
	VALUES_OPTS := $(VALUES_OPTS) -f $(SECRETS_FILE)
endif
ifneq ($(strip $(OVERRIDES_FILE)),)
	VALUES_OPTS := $(VALUES_OPTS) -f $(OVERRIDES_FILE)
endif

.PHONY: help lint template deploy undeploy up down test validate clean deploy-trust-k8s undeploy-trust-k8s dry-run update-deps sync-kit patch-kit-secrets xnat-init patch-aws-creds

help: ## Display this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

lint: ## Lint the Helm chart
	$(HELM) lint $(CHART_DIR)

template: ## Render Helm templates to stdout
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS)

dry-run: ## Render templates with --debug (dry-run equivalent)
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) --debug

update-deps: ## Update Helm chart dependencies
	$(HELM) dependency update $(CHART_DIR)

deploy: ## Deploy the Helm chart to the cluster
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	$(HELM) upgrade --install $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--timeout 20m \
		$(VALUES_OPTS)

undeploy: ## Remove the Helm release from the cluster
	$(HELM) uninstall $(RELEASE_NAME) --namespace $(NAMESPACE)

up: deploy ## Deploy the trust K8s release (alias for deploy)
	@true

down: undeploy ## Tear down the trust K8s release (alias for undeploy)
	@true

deploy-trust-k8s: deploy ## Deploy trust services to K8s; if KIT= is set, re-patches per-trust secrets without overwriting the override file
ifdef KIT
	@$(MAKE) patch-kit-secrets KIT=$(KIT)
endif

undeploy-trust-k8s: undeploy ## Remove trust services from K8s (alias for undeploy)
	@true

status: ## Show the status of the Helm release and run smoke tests
	@echo "=== Helm Release Status ==="
	$(HELM) status $(RELEASE_NAME) --namespace $(NAMESPACE)
	@echo ""
	@echo "=== Smoke Tests ==="
	@python3 $(CHART_DIR)/check_status.py --namespace $(NAMESPACE) --helm-release $(RELEASE_NAME)

list: ## List all Helm releases in the namespace
	$(HELM) list --namespace $(NAMESPACE)

test: lint template validate ## Run all validation steps

validate: ## Validate rendered templates against Kubernetes schema
	@if command -v kubeconform >/dev/null 2>&1; then \
		$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) | kubeconform --kubernetes-version 1.28 --strict; \
	else \
		echo "⚠️  kubeconform not installed; skipping schema validation"; \
	fi

template-all-backends: ## Render templates for all FL backend variants
	@echo "=== Default (nvflare) ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) > /dev/null
	@echo "=== Flower ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) --set flBackend=flower > /dev/null
	@echo "=== All disabled ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) \
		--set trustApi.enabled=false \
		--set imagingApi.enabled=false \
		--set dataAccessApi.enabled=false \
		--set flClient.enabled=false \
		--set omopDb.enabled=false \
		--set orthanc.enabled=false \
		--set xnat.enabled=false \
		--set observability.loki.enabled=false \
		--set observability.alloy.enabled=false \
		--set observability.grafana.enabled=false \
		> /dev/null
	@echo "✅ All backend variants rendered successfully"

clean: ## Clean up rendered output
	@rm -f $(CHART_DIR)/*.rendered.yaml

# Registration is done ONCE on the hub, not here:
#   make new-trust TRUST_CODE=<KIT> TRUST_NAME="..."
#   make -C deploy/providers/AWS register-trusts KIT=<KIT> PROD=<env>
#   make sync-trust-kit KIT=<KIT> PROD=<env>          # fills the Hub-shared block
# This target then reads trust/.env.<KIT>.<env>, patches the per-trust keys into the
# chart's Kubernetes Secret, and writes a secret-free Helm override. Then deploy with
# `make up OVERRIDES_FILE=k8s-trust-<KIT>.yaml`. Env auto-detected from $(PROD).
xnat-init: ## Run the XNAT post-install init job manually (use after XNAT web is ready, when initJob.enabled=false)
	@echo "▶  Rendering XNAT init job from chart templates..."
	@$(HELM) template $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$(VALUES_OPTS) \
		--set xnat.initJob.enabled=true \
		| kubectl apply -n $(NAMESPACE) -f - \
		--selector app.kubernetes.io/component=xnat-init
	@echo "⏳  Waiting for xnat-init job to complete (timeout 15m)..."
	@kubectl wait job \
		--selector app.kubernetes.io/component=xnat-init \
		--for=condition=complete \
		--namespace $(NAMESPACE) \
		--timeout=15m && \
	echo "✅  XNAT init complete." || \
	(echo "❌  XNAT init failed — check logs:"; \
	 kubectl logs -n $(NAMESPACE) -l app.kubernetes.io/component=xnat-init --tail=40; \
	 exit 1)

patch-aws-creds: ## Patch current AWS SSO credentials into the fl-client S3 secret (re-run after every helm upgrade or SSO token refresh)
	@python3 $(CHART_DIR)/patch_aws_creds.py \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets

patch-kit-secrets: ## Patch per-trust secrets from kit file into the cluster Secret WITHOUT overwriting the override YAML (safe after helm upgrade)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make patch-kit-secrets KIT=Trust_K8s PROD=stag"; exit 1)
	@python3 $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--output-dir $(CHART_DIR) \
		--no-write-override

sync-kit: ## Sync a registered trust KIT into the cluster (patches Secret + writes k8s-trust-<KIT>.yaml override)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make sync-kit KIT=Trust_K8s PROD=stag"; exit 1)
	@python3 $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--output-dir $(CHART_DIR)
