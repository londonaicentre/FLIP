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
SECRETS_FILE ?=

# Determine which values files to pass
ifneq ($(strip $(SECRETS_FILE)),)
	VALUES_OPTS := -f $(VALUES_FILE) -f $(SECRETS_FILE)
else
	VALUES_OPTS := -f $(VALUES_FILE)
endif

.PHONY: help lint template deploy undeploy test validate clean

help: ## Display this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

lint: ## Lint the Helm chart
	$(HELM) lint $(CHART_DIR)

template: ## Render Helm templates to stdout
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS)

deploy: ## Deploy the Helm chart to the cluster
	$(HELM) upgrade --install $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--create-namespace \
		$(VALUES_OPTS)

undeploy: ## Remove the Helm release from the cluster
	$(HELM) uninstall $(RELEASE_NAME) --namespace $(NAMESPACE)

status: ## Show the status of the Helm release
	$(HELM) status $(RELEASE_NAME) --namespace $(NAMESPACE)

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

register-trust: ## Register a K8s trust with the CENTRAL HUB (requires --hub-url)
	@[ -n "$(TRUST_NAME)" ] || (echo "❌  TRUST_NAME is required. Usage: make register-trust TRUST_NAME=Trust_2 HUB_URL=https://stag.flip.aicentre.co.uk/api"; exit 1)
	@[ -n "$(HUB_URL)" ] || (echo "❌  HUB_URL is required. Usage: make register-trust TRUST_NAME=Trust_2 HUB_URL=https://stag.flip.aicentre.co.uk/api"; exit 1)
	@python3 $(CHART_DIR)/register_k8s_trust.py \
		--trust-name $(TRUST_NAME) \
		--hub-url $(HUB_URL) \
		--output-dir $(CHART_DIR)
