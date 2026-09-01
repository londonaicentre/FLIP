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

# Where the FL participant kit lives ON THE NODE, and the uid that must own it.
# KIT_DEST is what flClient.kitHostPath is set to, so it must match what the chart
# mounts. `make sync-kit` renders kitHostPath from the kit file's FL_KIT_DIR, which
# is /opt/flip/fl-kit in every shipped kit (trust/.env.example) and the path the
# Ansible EC2/on-prem plays stage to — so that is the default here too. Overriding
# it means also passing --set flClient.kitHostPath=<same path> at deploy, or the pod
# stays Pending on a `hostPath type check failed` event naming the mounted path.
KIT_DEST ?= /opt/flip/fl-kit
# KIT_UID follows the backend's image user: Flower's upstream base runs as 49999,
# the NVFLARE image as 1000. Which backend is read from the kit ITSELF — an NVFLARE
# startup kit carries startup/ (with local/ and transfer/), a Flower kit carries
# certificates/ and keys/ — never from an FL_BACKEND that nothing here derives:
# defaulting to one backend when the variable is unset would chown a Flower kit to
# uid 1000 and leave the SuperNode unable to read its own certs, with nothing
# saying so until the pod logs. FL_BACKEND=<backend> on the command line still
# overrides the shape, and so does an explicit KIT_UID=. Resolved at parse time,
# scoped to the stage-kit goal, so `make -n stage-kit` shows the real chown and an
# unrecognisable kit fails before anything is copied.
KIT_SHAPE_NVFLARE := $(if $(wildcard $(KIT_SRC)/startup/),nvflare,)
KIT_SHAPE_FLOWER := $(if $(wildcard $(KIT_SRC)/certificates/ $(KIT_SRC)/keys/),flower,)
KIT_BACKEND := $(or $(FL_BACKEND),$(KIT_SHAPE_NVFLARE),$(KIT_SHAPE_FLOWER))
ifneq ($(filter stage-kit,$(MAKECMDGOALS)),)
ifeq ($(strip $(KIT_SRC)),)
$(error ❌  KIT_SRC is required — the local directory holding this trust's kit)
endif
ifeq ($(wildcard $(KIT_SRC)/.),)
$(error ❌  KIT_SRC $(KIT_SRC) is not a directory)
endif
ifneq ($(FL_BACKEND),)
ifeq ($(filter nvflare flower,$(FL_BACKEND)),)
$(error ❌  FL_BACKEND=$(FL_BACKEND) is not a backend — nvflare or flower)
endif
else ifeq ($(KIT_BACKEND),)
$(error ❌  cannot tell which backend the kit at $(KIT_SRC) is for: an NVFLARE kit carries startup/, a Flower kit certificates/ and keys/, and this has neither. Point KIT_SRC at the slot's kit directory, or pass FL_BACKEND=nvflare|flower to override)
else ifneq ($(and $(KIT_SHAPE_NVFLARE),$(KIT_SHAPE_FLOWER)),)
$(error ❌  the kit at $(KIT_SRC) has both an NVFLARE shape (startup/) and a Flower shape (certificates/ or keys/) — stage one slot's kit only, or pass FL_BACKEND=nvflare|flower to say which it is)
endif
endif
KIT_UID ?= $(if $(filter flower,$(KIT_BACKEND)),49999,1000)
# Which cluster to act on. Defaults to kubectl's current context, but stage-kit
# REQUIRES it explicitly: it writes to a node's filesystem, and a machine running
# several kind clusters will otherwise silently stage into whichever one kubectl
# happens to point at.
KUBE_CONTEXT ?=
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

.PHONY: help lint template deploy undeploy up down test validate clean deploy-trust-k8s undeploy-trust-k8s dry-run update-deps sync-kit patch-kit-secrets xnat-init stage-kit preflight template-egress-variants

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

preflight: ## Check all prerequisites before deploying (tools, cluster, files, kit)
	@PROD="$(PROD)" \
	 KIT="$(KIT)" \
	 NAMESPACE="$(NAMESPACE)" \
	 RELEASE_NAME="$(RELEASE_NAME)" \
	 OVERRIDES_FILE="$(OVERRIDES_FILE)" \
	 HELM="$(HELM)" \
	 bash $(CHART_DIR)/scripts/preflight.sh

stage-kit: ## Place this trust's FL participant kit onto the node (run BEFORE deploy)
	## The chart never fetches the kit — a trust holds no FLIP AWS credentials, and the
	## kit arrives out-of-band (trust/README.md). This copies an already-obtained kit
	## from KIT_SRC onto the node at the path the chart mounts (flClient.kitHostPath).
	##
	##   make stage-kit KIT_SRC=~/kits/Trust_2 KUBE_CONTEXT=kind-flip-kch
	##
	## KIT_DEST defaults to /opt/flip/fl-kit, which is what `make sync-kit` renders as
	## flClient.kitHostPath (from the kit's FL_KIT_DIR) — pass it only alongside a
	## matching --set flClient.kitHostPath at deploy time.
	## KIT_SRC contents are the slot's own kit: for NVFLARE local/ startup/ transfer/,
	## for Flower certificates/ and keys/ holding THIS slot's credential only. That
	## shape is also how the owning uid is chosen (KIT_UID above) — no FL_BACKEND
	## needed; a kit with neither or both shapes is refused before anything is copied.
	## On a real cluster the operator places it by whatever means the site allows; this
	## target implements the kind case, which is what dev and stag use.
	@[ -n "$(KUBE_CONTEXT)" ] || (echo "❌  KUBE_CONTEXT is required — this writes to a node's filesystem, and defaulting to kubectl's current context ($$(kubectl config current-context 2>/dev/null || echo none)) would stage into whichever cluster that happens to be. e.g. KUBE_CONTEXT=kind-flip-kch"; exit 1)
	@node=$$(kubectl --context $(KUBE_CONTEXT) get nodes -o jsonpath='{.items[0].metadata.name}') && \
	  [ -n "$$node" ] || { echo "❌  no node found in context $(KUBE_CONTEXT) — is the cluster reachable?"; exit 1; }; \
	  echo "📦 Staging $(KIT_SRC) ($(KIT_BACKEND) kit, owner uid $(KIT_UID)) → [$(KUBE_CONTEXT)] $$node:$(KIT_DEST)"; \
	  docker exec "$$node" mkdir -p "$(KIT_DEST)" && \
	  docker cp "$(KIT_SRC)/." "$$node:$(KIT_DEST)/" && \
	  docker exec "$$node" sh -c 'chmod -R go-rwx "$(KIT_DEST)" && chown -R $(KIT_UID):$(KIT_UID) "$(KIT_DEST)"' && \
	  echo "✅ Kit staged. Deploy with --set flClient.kitHostPath=$(KIT_DEST)"

deploy: preflight ## Deploy the Helm chart to the cluster
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

deploy-trust-k8s: ## Deploy trust services to K8s; KIT= selects the per-trust override file and re-patches secrets
ifdef KIT
	@rc=0; \
	$(MAKE) deploy OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml || rc=$$?; \
	if [ $$rc -ne 0 ]; then \
		echo "⚠️  deploy returned non-zero ($$rc) — often a non-fatal post-install hook (e.g. xnat-init — see #565), but it may be a genuine Helm failure. Patching per-trust secrets first so trust-api gets the correct key and does not poll with the stale seed key (#595), then re-exiting with the deploy status."; \
	fi; \
	$(MAKE) patch-kit-secrets KIT=$(KIT); \
	exit $$rc
else
	@$(MAKE) deploy
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
		$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) | kubeconform --kubernetes-version 1.28.0 --strict; \
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

template-egress-variants: ## Render the egress NetworkPolicy in default / hardened-CIDR / disabled shapes (#516 CI coverage)
	@echo "=== Default egress ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) --show-only templates/network-policy.yaml > /dev/null
	@echo "=== Hardened (CIDR-scoped) egress ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) \
		--set networkPolicies.allowedEgressCIDRs[0]=10.20.0.0/24 \
		--set networkPolicies.allowedEgressCIDRsWithPorts[0].cidrs[0]=13.43.162.56/32 \
		--set networkPolicies.allowedEgressCIDRsWithPorts[0].port=8002 \
		--show-only templates/network-policy.yaml > /dev/null
	@echo "=== NetworkPolicies disabled ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) \
		--set networkPolicies.enabled=false --show-only templates/network-policy.yaml || true
	@echo "✅ Egress NetworkPolicy variants render. See NETWORK-POLICY.md (#516)."

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

patch-kit-secrets: ## Patch per-trust secrets from kit file into the cluster Secret WITHOUT overwriting the override YAML (safe after helm upgrade)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make patch-kit-secrets KIT=Trust_K8s PROD=stag"; exit 1)
	@python3 $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR) \
		--no-write-override
	@for svc in trust-api data-access-api imaging-api; do \
		kubectl rollout restart deployment/$(RELEASE_NAME)-flip-trust-$$svc -n $(NAMESPACE); \
	done
	@for svc in trust-api data-access-api imaging-api; do \
		kubectl rollout status deployment/$(RELEASE_NAME)-flip-trust-$$svc -n $(NAMESPACE) --timeout=120s; \
	done

sync-kit: ## Sync a registered trust KIT into the cluster (patches Secret + writes k8s-trust-<KIT>.yaml override)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make sync-kit KIT=Trust_K8s PROD=stag"; exit 1)
	@python3 $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR)
