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
# the FLARE image as 1000. Which backend is read from the kit ITSELF — a FLARE
# startup kit carries startup/ (with local/ and transfer/), a Flower kit carries
# certificates/ and keys/ — never from an FL_BACKEND that nothing here derives:
# defaulting to one backend when the variable is unset would chown a Flower kit to
# uid 1000 and leave the SuperNode unable to read its own certs, with nothing
# saying so until the pod logs. FL_BACKEND=<backend> on the command line still
# overrides the shape, and so does an explicit KIT_UID=. Resolved at parse time,
# scoped to the stage-kit goal, so `make -n stage-kit` shows the resolved owner uid and an
# unrecognisable kit fails before anything is copied.
KIT_SHAPE_NVFLARE := $(if $(wildcard $(KIT_SRC)/startup/),nvflare,)
KIT_SHAPE_FLOWER := $(if $(wildcard $(KIT_SRC)/certificates/ $(KIT_SRC)/keys/),flower,)
KIT_BACKEND := $(or $(FL_BACKEND),$(KIT_SHAPE_NVFLARE),$(KIT_SHAPE_FLOWER))
# The subdirectories the chart mounts out of the kit by subPath (templates/fl-client.yaml).
# They must exist under KIT_DEST before the pod starts: a subPath that is missing from the
# hostPath is created by kubelet as root:root 0700, and the non-root fl-client then cannot
# write it — while the pod still reports Running, so nothing surfaces until FLARE writes the
# trained model into transfer/ at the END of a run, or site policy writes privacy.json into
# local/ at container start. `aws s3 sync` and `docker cp` both mirror objects rather than
# empty prefixes, so transfer/ in particular never survives a sync-assembled kit. This is the
# same defect the EC2 play fixes with its post-sync `mkdir -p "$$dest/transfer"`.
KIT_SUBDIRS := $(if $(filter flower,$(KIT_BACKEND)),certificates keys,local startup transfer)
ifneq ($(filter stage-kit,$(MAKECMDGOALS)),)
ifeq ($(strip $(KIT_SRC)),)
$(error ❌  KIT_SRC is required — the local directory holding this trust's kit)
endif
ifeq ($(wildcard $(KIT_SRC)/.),)
$(error ❌  KIT_SRC $(KIT_SRC) is not a directory)
endif
# stage-kit empties KIT_DEST before copying (see the recipe), so refuse the two values
# where that would reach beyond the kit directory. An empty KIT_DEST would make the wipe
# `find  -mindepth 1 -delete`, which walks the working directory, and `/` would walk the
# node's root filesystem. Both are only reachable by an explicit override, but the wipe is
# the one step here that destroys rather than writes, so it gets a parse-time guard.
ifeq ($(strip $(KIT_DEST)),)
$(error ❌  KIT_DEST must not be empty — stage-kit clears it before copying)
endif
ifeq ($(strip $(KIT_DEST)),/)
$(error ❌  KIT_DEST must not be / — stage-kit clears it before copying)
endif
ifneq ($(FL_BACKEND),)
ifeq ($(filter nvflare flower,$(FL_BACKEND)),)
$(error ❌  FL_BACKEND=$(FL_BACKEND) is not a backend — nvflare or flower)
endif
else ifeq ($(KIT_BACKEND),)
$(error ❌  cannot tell which backend the kit at $(KIT_SRC) is for: a FLARE kit carries startup/, a Flower kit certificates/ and keys/, and this has neither. Point KIT_SRC at the slot's kit directory, or pass FL_BACKEND=nvflare|flower to override)
else ifneq ($(and $(KIT_SHAPE_NVFLARE),$(KIT_SHAPE_FLOWER)),)
$(error ❌  the kit at $(KIT_SRC) has both a FLARE shape (startup/) and a Flower shape (certificates/ or keys/) — stage one slot's kit only, or pass FL_BACKEND=nvflare|flower to say which it is)
endif
endif
KIT_UID ?= $(if $(filter flower,$(KIT_BACKEND)),49999,1000)
# The OMOP core vocabulary (FLIP#923): VOCAB_DIR is a folder of Athena CSVs (CONCEPT.csv, ...) and
# VOCAB_DEST the node directory omopDb.vocabLoad.hostPath names (the kit's OMOP_VOCAB_DIR).
# stage-vocab empties VOCAB_DEST first, as stage-kit does KIT_DEST and with the same guards.
VOCAB_DEST ?= /opt/flip/omop-vocab
VOCAB_BUNDLE ?= vocab_aicentre_core_20240916
ifneq ($(filter stage-vocab,$(MAKECMDGOALS)),)
ifeq ($(strip $(VOCAB_DEST)),)
$(error ❌  VOCAB_DEST must not be empty — stage-vocab clears it before copying)
endif
ifeq ($(strip $(VOCAB_DEST)),/)
$(error ❌  VOCAB_DEST must not be / — stage-vocab clears it before copying)
endif
ifeq ($(wildcard $(VOCAB_DIR)/CONCEPT.csv),)
$(error ❌  VOCAB_DIR=<folder holding the unpacked bundle: CONCEPT.csv, ...> is required — make download-vocab, or an OHDSI Athena export)
endif
endif
# Which cluster to act on. Defaults to kubectl's current context, but stage-kit
# REQUIRES it explicitly: it writes to a node's filesystem, and a machine running
# several kind clusters will otherwise silently stage into whichever one kubectl
# happens to point at. When set it reaches every helm / kubectl call below and the
# sync script, so `make upgrade-trust-k8s … KUBE_CONTEXT=<ctx>` moves that cluster only.
KUBE_CONTEXT ?=
HELM_BIN := helm
KUBECTL := kubectl$(if $(KUBE_CONTEXT), --context $(KUBE_CONTEXT))
HELM := $(HELM_BIN)$(if $(KUBE_CONTEXT), --kube-context $(KUBE_CONTEXT))
KUBE_CONTEXT_ARG := $(if $(KUBE_CONTEXT),--kube-context $(KUBE_CONTEXT))
RELEASE_NAME ?= trust-release
CHART_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
VALUES_FILE ?= $(CHART_DIR)/values.yaml

# REPO_ROOT is where trust/deploy/scripts/site_upgrade.py and the shared make fragments live.
REPO_ROOT := $(abspath $(CHART_DIR)/../../..)
# Kit-file env token (ENV) from HUB, derived once for every Makefile that reads HUB.
include $(REPO_ROOT)/deploy/env_mode.mk
SECRETS_FILE := $(wildcard $(CHART_DIR)/values-secrets.yaml)
OVERRIDES_FILE ?=
# The trust kit this chart deployment is synced from (trust/.env.<CODE>.<env>, or an old
# name resolved by env_mode.mk's kit_file), handed to every script that reads it.
ifeq ($(origin KIT_FILE),undefined)
KIT_FILE := $(call kit_file,$(REPO_ROOT)/trust/,$(KIT))
endif
$(if $(KIT),$(call kit_hint,$(patsubst $(REPO_ROOT)/%,%,$(KIT_FILE))))

# One budget governs every wait this Makefile performs on the XNAT init job, because
# there is only one job and two ways of waiting for it: `deploy` waits on it as a
# post-upgrade Helm hook, `xnat-init` waits on it directly with kubectl. Those used to
# carry two different hardcoded literals (20m and 15m), both below the job's real
# duration on a slow cluster — an upgrade that had already applied the corrected pod
# spec would then report failure, and the operator would read a Helm timeout rather than
# the wait that actually expired (FLIP#1228). The default is above the ~22m observed on
# a real trust cluster. The job's own internal budgets (templates/xnat-init-job.yaml)
# already chain to 40-90 minutes, so THIS is what bounds a deploy in practice — but it
# bounds the WAIT, not the Job: the hook sets no activeDeadlineSeconds and its delete
# policy is before-hook-creation,hook-succeeded, so when this expires Helm stops waiting
# and marks the release failed while the Job carries on running in the cluster. Raising
# one of those internal budgets alone therefore changes nothing about how long a deploy
# waits — raise this too, or the extra budget is unreachable.
HELM_TIMEOUT ?= 30m

# GNU make runs any recipe line whose text contains "$(MAKE)" even under -n, so a sub-target
# spelled that way would make `make -n deploy` deploy for real. Indirecting through a variable
# keeps the recursion without that flag; `make -n` then prints the step like any other line.
SUBMAKE := $(MAKE)

# Automatic upgrades (FLIP#1644): AUTO_UPGRADE=1, or K8S_AUTO_UPGRADE=true in the kit file so that every deploy and
# upgrade of that trust keeps it. Three things change together, because the in-cluster agent needs all three:
#   - HELM_DRIVER=configmap, so the release is recorded where the agent (which has no `secrets` verbs) can see it;
#   - the trust Secret moves out of the release: values-secrets.yaml is applied with kubectl (apply-trust-secret)
#     and the chart gets secrets.create=false, so no password is stored in the release record the agent reads;
#   - autoUpgrade.enabled=true.
# preflight refuses a deploy whose driver cannot see the live release (release_driver.py --deploy-driver), because
# `helm upgrade --install` would then put a second release on top of it. So switching an EXISTING trust on needs
# its release record moved to ConfigMaps first, and a trust that is on cannot be deployed without it.
AUTO_UPGRADE ?= $(if $(KIT),$(shell sed -n 's/^K8S_AUTO_UPGRADE=//p' $(KIT_FILE) 2>/dev/null | tail -1))
AUTO_UPGRADE_ON := $(filter 1 true yes,$(strip $(AUTO_UPGRADE)))
# The name the chart gives its own Secret, and the one patch-kit-secrets patches.
TRUST_SECRET_NAME ?= $(RELEASE_NAME)-flip-trust-secrets
DEPLOY_HELM_DRIVER := $(if $(AUTO_UPGRADE_ON),configmap,secret)
ifneq ($(AUTO_UPGRADE_ON),)
export HELM_DRIVER := configmap
endif

# Determine which values files to pass
VALUES_OPTS := -f $(VALUES_FILE)
ifneq ($(AUTO_UPGRADE_ON),)
	VALUES_OPTS := $(VALUES_OPTS) --set autoUpgrade.enabled=true --set secrets.create=false \
		--set secrets.existingName=$(TRUST_SECRET_NAME)
else ifneq ($(strip $(SECRETS_FILE)),)
	VALUES_OPTS := $(VALUES_OPTS) -f $(SECRETS_FILE)
endif
ifneq ($(strip $(OVERRIDES_FILE)),)
	VALUES_OPTS := $(VALUES_OPTS) -f $(OVERRIDES_FILE)
endif
# Platform overlays (space-separated, applied in order) after the per-trust override, e.g. values-aks.yaml on a managed
# AKS cluster (FLIP#1390): settings that belong to where the chart runs, not to the trust. With KIT set they default
# to the kit file's K8S_PLATFORM_VALUES, so every deploy and upgrade of that trust applies them: a run that dropped
# values-aks.yaml would move PGDATA back to the volume root and the kit back to the node.
PLATFORM_VALUES ?= $(if $(KIT),$(shell sed -n 's/^K8S_PLATFORM_VALUES=//p' $(KIT_FILE) 2>/dev/null | tail -1))
ifneq ($(strip $(PLATFORM_VALUES)),)
	VALUES_OPTS := $(VALUES_OPTS) $(foreach f,$(PLATFORM_VALUES),-f $(f))
endif
# A kit with TRUST_DATA_SEED=false (FLIP#1687) turns the seed hook off on every render and deploy that names its KIT,
# not only through the sync-kit override, so a missing or stale override cannot seed a real trust's OMOP. As --set it
# beats every values file. Any other value stops `deploy` at preflight, as sync-kit stops at it.
KIT_TRUST_DATA_SEED := $(if $(KIT),$(call kit_trust_data_seed,$(KIT_FILE)),true)
ifeq ($(KIT_TRUST_DATA_SEED),false)
	VALUES_OPTS := $(VALUES_OPTS) --set trustData.seed.enabled=false
endif

# The chart requires flClient.kitHostPath whenever flClient is enabled (with the default hostPath kit source), so any
# render (make test, template-all-backends, a plain `make template`) fails the
# moment values.yaml has no default. CI renders with the path hardcoded (.github/
# workflows/test_helm_chart.yml); default the same value here so the local
# targets pass and an operator can override at deploy with
# --set flClient.kitHostPath=<path> matching where the kit was actually staged.
# Pass FL_CLIENT_KIT_HOST_PATH= (empty) to render without the default, e.g. to
# exercise the chart's required() guard.
FL_CLIENT_KIT_HOST_PATH ?= $(KIT_DEST)
ifneq ($(strip $(FL_CLIENT_KIT_HOST_PATH)),)
	VALUES_OPTS := $(VALUES_OPTS) --set flClient.kitHostPath=$(FL_CLIENT_KIT_HOST_PATH)
endif

# The release pin (FLIP#1204): TAG=vX.Y.Z sets global.image.tag, which every FLIP-built
# image follows (templates/_helpers.tpl flip-trust.imageTag). Last so it wins over the
# override file's value, which sync-kit fills from the kit's DOCKER_TAG.
TAG ?=
ifneq ($(strip $(TAG)),)
	VALUES_OPTS := $(VALUES_OPTS) --set global.image.tag=$(TAG)
endif

.PHONY: sync-project-studies help lint template deploy deploy-release failing-hooks undeploy up down test validate clean deploy-trust-k8s undeploy-trust-k8s upgrade-trust-k8s dry-run update-deps sync-kit sync-kit-override patch-kit-secrets xnat-init kit-secret stage-kit download-vocab stage-vocab preflight apply-trust-secret migrate-release-driver template-egress-variants smoke-cstore selftest xnat-db-migrate-dump xnat-db-remove-old-cluster

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
	@case "$(KIT_TRUST_DATA_SEED)" in true|false) ;; *) \
		echo "❌ TRUST_DATA_SEED must be true or false in $(KIT_FILE), not '$(KIT_TRUST_DATA_SEED)'."; exit 1;; esac
	@HUB="$(HUB)" \
	 KIT="$(KIT)" \
	 KIT_FILE="$(KIT_FILE)" \
	 NAMESPACE="$(NAMESPACE)" \
	 RELEASE_NAME="$(RELEASE_NAME)" \
	 OVERRIDES_FILE="$(OVERRIDES_FILE)" \
	 HELM="$(HELM_BIN)" \
	 KUBE_CONTEXT="$(KUBE_CONTEXT)" \
	 bash $(CHART_DIR)/scripts/preflight.sh
	@python3 $(CHART_DIR)/scripts/release_driver.py --release $(RELEASE_NAME) --namespace $(NAMESPACE) \
		--kubectl "$(KUBECTL)" --deploy-driver $(DEPLOY_HELM_DRIVER)

# AUTO_UPGRADE only: the trust Secret, applied from values-secrets.yaml with kubectl rather than as part of the
# release, so the passwords never enter the release record. Rendered by the chart's own template so its keys and
# encoding cannot drift from what the services read; autoUpgrade is forced off for this one render, since the chart
# refuses secrets.create alongside it.
apply-trust-secret: ## (AUTO_UPGRADE) Apply the trust Secret from values-secrets.yaml, outside the Helm release
	@[ -n "$(SECRETS_FILE)" ] || { echo "❌ $(CHART_DIR)/values-secrets.yaml is required (make sync-kit KIT=<CODE> writes it)"; exit 1; }
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) --namespace $(NAMESPACE) $(VALUES_OPTS) -f $(SECRETS_FILE) \
		--set secrets.create=true --set autoUpgrade.enabled=false --show-only templates/secrets.yaml \
		| $(KUBECTL) apply --namespace $(NAMESPACE) -f -

# FLIP#1997: a trust deployed before automatic upgrades has its release recorded in Secrets, which the agent cannot
# see. This moves the record to ConfigMaps (every revision, with the passwords taken out of the values and the chart's
# Secret out of the stored manifest) and touches no workload or volume. A plan unless CONFIRM=1; a second run is a
# no-op. Afterwards the trust deploys with AUTO_UPGRADE=1. Rehearsed by trust/deploy/e2e/upgrade-agent/run.sh migrate.
migrate-release-driver: ## (AUTO_UPGRADE) Move an existing trust's Helm release record from Secrets to ConfigMaps; CONFIRM=1 applies
	python3 $(CHART_DIR)/scripts/migrate_release_driver.py --release $(RELEASE_NAME) --namespace $(NAMESPACE) \
		--kubectl "$(KUBECTL)" --helm "$(HELM)" $(if $(filter 1,$(CONFIRM)),--apply)

kit-secret: ## Put this trust's FL kit in a Secret for flClient.kit.source=secret (managed node pools, e.g. AKS)
	@[ -n "$(KIT_SRC)" ] || { echo "❌ KIT_SRC=<the slot's kit directory: local/ startup/ … or certificates/ keys/> is required"; exit 1; }
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	python3 $(CHART_DIR)/scripts/kit_secret.py --kit-src $(KIT_SRC) \
		--name $(RELEASE_NAME)-flip-trust-fl-kit --namespace $(NAMESPACE) | $(KUBECTL) apply -f -

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
	## KIT_SRC contents are the slot's own kit: for FLARE local/ startup/ transfer/,
	## for Flower certificates/ and keys/ holding THIS slot's credential only. That
	## shape is also how the owning uid is chosen (KIT_UID above) — no FL_BACKEND
	## needed; a kit with neither or both shapes is refused before anything is copied.
	## scripts/stage-to-node.sh does the copy (FLIP#1958): on a kind node with docker exec/cp, on
	## any other node (k3s, a VM) through a short-lived root pod pinned to it, with KIT_DEST as a
	## hostPath and kubectl cp. A namespace under Pod Security "baseline" refuses that pod and the
	## chart's own kit hostPath alike: use the kit Secret route there (flClient.kit.source=secret,
	## `make kit-secret`).
	##
	## KIT_DEST's previous contents are replaced, not merged, for the same reason the EC2 play
	## wipes before its sync: re-staging after a kit-date bump, a backend switch, or a corrected
	## over-broad sync would otherwise leave credentials that should no longer be on the node.
	## The mounted subdirectories are created and the whole kit is made private to KIT_UID.
	##
	## With several nodes, NODE=<name> is required: the node the FL client runs on, which is
	## imaging-api's unless flClient.affinity says otherwise. One node needs no NODE.
	@[ -n "$(KUBE_CONTEXT)" ] || (echo "❌  KUBE_CONTEXT is required — this writes to a node's filesystem, and defaulting to kubectl's current context ($$(kubectl config current-context 2>/dev/null || echo none)) would stage into whichever cluster that happens to be. e.g. KUBE_CONTEXT=kind-flip-kch"; exit 1)
	@echo "📦 Staging $(KIT_SRC) ($(KIT_BACKEND) kit, owner uid $(KIT_UID)) → [$(KUBE_CONTEXT)] $(KIT_DEST)"
	NAMESPACE=$(NAMESPACE) $(CHART_DIR)/scripts/stage-to-node.sh --context "$(KUBE_CONTEXT)" \
	  --src "$(KIT_SRC)" --dest "$(KIT_DEST)" --subdirs "$(KIT_SUBDIRS)" --owner $(KIT_UID) $(if $(NODE),--node "$(NODE)")
	@echo "✅ Kit staged. Deploy with --set flClient.kitHostPath=$(KIT_DEST)"

download-vocab: ## Download the OMOP core vocabulary from the AI Centre bucket to this host (your AWS CLI profile)
	## For operators with access to the bucket (FLIP#923); everyone else exports the same
	## folder from OHDSI Athena (trust/omop-db/README.md). Runs on this host, never in the
	## cluster. VOCAB_S3_BUCKET overrides the bucket.
	$(MAKE) -C $(REPO_ROOT)/trust/omop-db fetch-vocab-core VOCAB_CORE_BUNDLE=$(VOCAB_BUNDLE) $(if $(VOCAB_S3_BUCKET),VOCAB_S3_BUCKET=$(VOCAB_S3_BUCKET))
	@echo "✅ Next: make stage-vocab VOCAB_DIR=$(REPO_ROOT)/trust/omop-db/data/$(VOCAB_BUNDLE) KUBE_CONTEXT=<ctx>"

stage-vocab: ## Place the unpacked OMOP core vocabulary onto the node (omopDb.vocabLoad.hostPath; run BEFORE deploy)
	## Like stage-kit, through scripts/stage-to-node.sh (kind: docker cp; any other node: a pod):
	##
	##   make stage-vocab VOCAB_DIR=<folder with CONCEPT.csv, ...> KUBE_CONTEXT=<context>
	##
	## With several nodes, name one with NODE=<name> and pin the load Job there
	## (omopDb.vocabLoad.nodeSelector). It is read only for the first load. World-readable, as
	## the loader runs as the image user.
	##
	## VOCAB_DEST is emptied first: a copy merges, so a narrower re-stage would otherwise leave
	## the previous bundle's table files beside the new ones for the loader to read.
	@[ -n "$(KUBE_CONTEXT)" ] || (echo "❌  KUBE_CONTEXT is required — this writes to a node's filesystem. e.g. KUBE_CONTEXT=kind-flip-kch"; exit 1)
	@echo "📦 Staging $(VOCAB_DIR) → [$(KUBE_CONTEXT)] $(VOCAB_DEST)"
	NAMESPACE=$(NAMESPACE) $(CHART_DIR)/scripts/stage-to-node.sh --context "$(KUBE_CONTEXT)" \
	  --src "$(VOCAB_DIR)" --dest "$(VOCAB_DEST)" --world-readable $(if $(NODE),--node "$(NODE)")
	@echo "✅ Vocabulary staged. Deploy with --set omopDb.vocabLoad.hostPath=$(VOCAB_DEST) (or OMOP_VOCAB_DIR=$(VOCAB_DEST) in the kit)"

# Deliberately NO --force-conflicts here. Helm 4 applies the release server-side, and
# `patch-kit-secrets` writes the kit's per-trust keys into the same Secret with field
# manager `kubectl-patch`, so an upgrade that would CHANGE one of those keys is refused
# with `Apply failed with N conflicts: conflict with "kubectl-patch"` and the release
# stays on its last working revision. That refusal is a safety property, not a defect:
# forcing it would overwrite the trust's live API key with whatever values-secrets.yaml
# happens to hold — a stale value, or none at all (templates/secrets.yaml omits an empty
# slot) — and the trust would poll the hub with a dead credential after a routine
# redeploy. The conflict is removed at the source instead: sync-kit / patch-kit-secrets
# realign values-secrets.yaml with what they patch, so helm's apply is a no-op on those
# fields (sync_k8s_kit.align_values_secrets). Helm 3 never applies server-side and is
# unaffected either way, so no version switch is needed.
#
# A Deployment the chart now renders as Recreate but the cluster still runs as RollingUpdate is
# switched first, or Helm 4's server-side apply refuses it (FLIP#1524; scripts/migrate-strategy.sh).
deploy-release: preflight ## Apply the chart and wait for the core workloads to roll out (no xnat-init)
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(if $(AUTO_UPGRADE_ON),@$(SUBMAKE) --no-print-directory apply-trust-secret)
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) --namespace $(NAMESPACE) $(VALUES_OPTS) | \
		NAMESPACE=$(NAMESPACE) RELEASE_NAME=$(RELEASE_NAME) KUBECTL="$(KUBECTL)" $(CHART_DIR)/scripts/migrate-strategy.sh
	$(HELM) upgrade --install $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--timeout $(HELM_TIMEOUT) \
		--set xnat.initJob.enabled=false \
		$(VALUES_OPTS) \
		|| { $(SUBMAKE) --no-print-directory failing-hooks; exit 1; }
	@python3 $(CHART_DIR)/scripts/rollout_wait.py \
		--namespace $(NAMESPACE) --release $(RELEASE_NAME) \
		--timeout $(HELM_TIMEOUT) --kubectl "$(KUBECTL)" \
		|| { $(SUBMAKE) --no-print-directory failing-hooks; exit 1; }

# The two steps are separate targets so that WHICH one failed is the signal. GNU make exits 2 for
# any failed recipe whatever code the recipe returned, so an exit code cannot carry "upgraded, but
# XNAT not configured" through make (FLIP#1645). A caller that must tell the two apart (the
# auto-upgrade agent, FLIP#1640; deploy-trust-k8s; upgrade-trust-k8s) runs deploy-release, then
# xnat-init, and reads which command failed. `deploy` is the two in sequence, for humans.
deploy: deploy-release ## Deploy the Helm chart to the cluster (deploy-release, then xnat-init)
	@$(SUBMAKE) --no-print-directory xnat-init || { \
		printf '\033[0;33m%s\033[0m\n' \
			"⚠️  the release is live and rolled out, but xnat-init did not complete (see #565)." \
			"⚠️  Re-run: make -C trust/deploy/helm xnat-init (raise HELM_TIMEOUT if it just needed longer)."; \
		exit 1; }

# Helm records every hook's outcome for the current revision, so when the upgrade fails the
# operator is told which one failed instead of reading the whole output. Best-effort: a cluster
# that cannot be read prints nothing and never changes the exit code of the caller.
failing-hooks: ## Name the post-upgrade hooks that did not succeed on the current revision
	-@$(HELM) status $(RELEASE_NAME) --namespace $(NAMESPACE) -o json 2>/dev/null \
		| python3 $(CHART_DIR)/scripts/failing_hooks.py 2>/dev/null

undeploy: ## Remove the Helm release from the cluster
	$(HELM) uninstall $(RELEASE_NAME) --namespace $(NAMESPACE)

up: deploy ## Deploy the trust K8s release (alias for deploy)
	@true

down: undeploy ## Tear down the trust K8s release (alias for undeploy)
	@true

# With KIT=, the override is regenerated from the kit first (sync-kit-override): it is
# generated, never edited, and redeploying the file an earlier sync-kit left behind would
# ship a stale governance document (FLIP#1259) — and a document edit would never reach the
# pods. upgrade-trust-k8s already regenerates it the same way.
deploy-trust-k8s: ## Deploy trust services to K8s; KIT= regenerates the per-trust override from the kit and re-patches secrets
ifdef KIT
	$(MAKE) sync-kit-override KIT=$(KIT)
	@rc=0; step=release; \
	$(MAKE) deploy-release OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml || rc=1; \
	if [ $$rc -eq 0 ]; then \
		step=xnat-init; \
		$(MAKE) xnat-init OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml || rc=1; \
	fi; \
	if [ $$rc -ne 0 ] && [ $$step = xnat-init ]; then \
		echo "⚠️  the release is live and rolled out, but xnat-init did not complete (see #565). Patching per-trust secrets, then failing."; \
	elif [ $$rc -ne 0 ]; then \
		echo "❌ the release did not land (helm or the rollout wait failed). Patching per-trust secrets first so trust-api gets the correct key and does not poll with the stale seed key (#595), then failing."; \
	fi; \
	$(MAKE) patch-kit-secrets KIT=$(KIT); \
	exit $$rc
else
	@$(MAKE) deploy
endif

undeploy-trust-k8s: undeploy ## Remove trust services from K8s (alias for undeploy)
	@true

smoke-cstore: ## Send a real C-STORE through the PACS and verify XNAT received it (FLIP#1228)
	## `make status` checks XNAT lists every plugin and answers its DQR route; this drives
	## the path those plugins are on. Both are needed: every plugin can load while the
	## importer is broken, and a C-ECHO can pass while every store aborts.
	## Requires a seeded Orthanc (it stores an instance the PACS already holds).
	## Exit 2 from the script is INCONCLUSIVE — the only evidence was a prearchive object's
	## arrival time while received.log exists and gained no line. Not a pass, and not enough
	## to fail a deploy on, so it is surfaced as a loud warning and this target still exits 0.
	## 1 (a real failure) and 0 (a pass) are passed straight through.
	@NAMESPACE=$(NAMESPACE) RELEASE_NAME=$(RELEASE_NAME) KUBE_CONTEXT=$(KUBE_CONTEXT) \
		$(CHART_DIR)/scripts/smoke-cstore.sh; \
	status=$$?; \
	if [ $$status -eq 2 ]; then \
		printf '\033[0;33m%s\033[0m\n' \
			"⚠️  C-STORE smoke INCONCLUSIVE (exit 2) — the receiver showed only an arrival-time match" \
			"⚠️  while received.log exists and gained no line. This is NOT a verified DICOM path:" \
			"⚠️  re-run with the PACS idle, and see TROUBLESHOOTING.md §2.7 before trusting it." >&2; \
		exit 0; \
	fi; \
	exit $$status

# Move a deployed Kubernetes trust to a release, data intact (FLIP#1204). A plain
# `helm upgrade` with the pin set: PVCs survive it, the init hooks short-circuit on
# populated data, and the API deployments roll onto the new images. The kit-side twin
# of the compose path's `make -C trust upgrade-trust` — the resolver pins the kit too, so
# the next `sync-kit` regenerates the same tag into the override rather than reverting it.
#   make upgrade-trust-k8s KIT=<CODE> [TAG=vX.Y.Z] [FL_TAG=vX.Y.Z] [HUB=<env>] [KUBE_CONTEXT=<ctx>]
# The chart templates come from this checkout, so a release TAG is refused unless the
# checkout is at that tag (ALLOW_CHECKOUT_DRIFT=1 overrides — testing a branch).
# TAG defaults to the release the hub runs (site_upgrade.py reads it from the kit's hub);
# FL_TAG pins the FL client apart from the rest (sha- moves only).
upgrade-trust-k8s: ## Move a deployed trust to a release tag (data-safe helm upgrade)
	@[ -n "$(KIT)" ] || { echo "❌ KIT=<CODE> is required"; exit 1; }
	@[ -f "$(CHART_DIR)/k8s-trust-$(KIT).yaml" ] || { echo "❌ $(CHART_DIR)/k8s-trust-$(KIT).yaml not found — run make sync-kit KIT=$(KIT) first"; exit 1; }
	uv run --no-config $(REPO_ROOT)/trust/deploy/scripts/site_upgrade.py plan --kit-file $(KIT_FILE) $(if $(TAG),--tag $(TAG)) $(if $(FL_TAG),--fl-tag $(FL_TAG)) $(if $(FORCE),--force) $(if $(YES),--yes) $(if $(ALLOW_CHECKOUT_DRIFT),--allow-checkout-drift)
	$(MAKE) _upgrade-trust-k8s-apply KIT=$(KIT) HUB=$(HUB)

# Phase 2: regenerate the override from the kit the resolver just pinned — that is how
# global.image.tag and the per-image pins (omopDb/orthanc/xnat/flClient image.pin, from the
# kit's OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG / DOCKER_FL_TAG) reach Helm — re-patch the
# Secret from the same kit, then a plain `helm upgrade` at that tag.
_upgrade-trust-k8s-apply:
	$(MAKE) sync-kit KIT=$(KIT)
	$(MAKE) xnat-db-migrate-dump
	$(MAKE) deploy-release OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml TAG=$(shell sed -n 's/^DOCKER_TAG=//p' $(KIT_FILE) | tail -1) \
	  || { $(MAKE) xnat-db-drop-unused-dump AFTER_FAILED_DEPLOY=1; exit 1; }
	$(MAKE) xnat-db-drop-unused-dump
	$(MAKE) xnat-init OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml TAG=$(shell sed -n 's/^DOCKER_TAG=//p' $(KIT_FILE) | tail -1) \
	  || { echo "⚠️  the release is live and rolled out, but xnat-init did not complete (see #565). Re-run: make -C trust/deploy/helm xnat-init"; exit 1; }
	@echo "✅ $(KIT) is on $$(sed -n 's/^DOCKER_TAG=//p' $(KIT_FILE) | tail -1). Verify: kubectl -n $(NAMESPACE) get pods; trust-api /health reports the new version."

# The PostgreSQL major the xnat-db image runs, and the release's XNAT pods (FLIP#1812).
XNAT_DB_PG_MAJOR := $(shell sed -n 's/^PG_VERSION=\([0-9]*\).*/\1/p' $(REPO_ROOT)/trust/xnat/.env)
XNAT_POD = -n $(NAMESPACE) -l app.kubernetes.io/instance=$(RELEASE_NAME),app.kubernetes.io/component

# Before a helm upgrade that moves xnat-db to a newer PostgreSQL major: stop xnat-web, then dump the
# xnat database beside the running cluster on its volume, where the new image restores it on first
# start. The helm upgrade brings xnat-web back. The old cluster stays for a rollback until
# xnat-db-remove-old-cluster. A no-op without an xnat-db pod or once it is on that major.
xnat-db-migrate-dump:
	@set -e; \
	pod=$$($(KUBECTL) get pod $(XNAT_POD)=xnat-db --field-selector=status.phase=Running -o name | head -1); \
	[ -n "$$pod" ] || exit 0; \
	current=$$($(KUBECTL) exec -n $(NAMESPACE) "$$pod" -c postgres -- sh -c 'cat "$$PGDATA/PG_VERSION"'); \
	case "$$current:$(XNAT_DB_PG_MAJOR)" in *[!0-9:]*|:*|*:) \
	  echo "❌ Cannot compare xnat-db's PostgreSQL major ('$$current') with the image's ('$(XNAT_DB_PG_MAJOR)', from PG_VERSION in trust/xnat/.env). Nothing was changed."; exit 1 ;; esac; \
	[ "$$current" -lt $(XNAT_DB_PG_MAJOR) ] || exit 0; \
	echo "⬆️  xnat-db moves from PostgreSQL $$current to $(XNAT_DB_PG_MAJOR): stopping xnat-web and dumping the xnat database for the new server to restore."; \
	$(KUBECTL) scale deployment $(XNAT_POD)=xnat-web --replicas=0; \
	$(KUBECTL) wait pod $(XNAT_POD)=xnat-web --for=delete --timeout=$(HELM_TIMEOUT) \
	  || ! $(KUBECTL) get pod $(XNAT_POD)=xnat-web -o name | grep -q .; \
	$(KUBECTL) exec -i -n $(NAMESPACE) "$$pod" -c postgres -- sh -s $(XNAT_DB_PG_MAJOR) \
	  < $(REPO_ROOT)/trust/xnat/scripts/xnat_db_migrate_dump.sh || { \
	  echo "❌ The dump failed, so nothing was upgraded. Starting xnat-web again on PostgreSQL $$current."; \
	  $(KUBECTL) scale deployment $(XNAT_POD)=xnat-web --replicas=1; exit 1; }

# After the helm upgrade: remove a dump the deploy did not use, because xnat-db stayed on the old
# major (xnat.image.pin or the kit's XNAT_TAG holds an older release). helm upgrade does not wait
# for the StatefulSet, and the pod it is replacing still answers with the dump the new one needs, so
# the rollout comes first. After a failed deploy the old server may be the one that stays, and the
# dump goes all the same.
xnat-db-drop-unused-dump:
	@set -e; \
	sts=$$($(KUBECTL) get statefulset $(XNAT_POD)=xnat-db -o name | head -1); \
	[ -n "$$sts" ] || exit 0; \
	$(KUBECTL) rollout status -n $(NAMESPACE) "$$sts" --timeout=$(HELM_TIMEOUT)$(if $(AFTER_FAILED_DEPLOY), || true); \
	pod=$$($(KUBECTL) get pod $(XNAT_POD)=xnat-db --field-selector=status.phase=Running -o name | head -1); \
	[ -z "$$pod" ] || $(KUBECTL) exec -i -n $(NAMESPACE) "$$pod" -c postgres -- sh -s \
	  < $(REPO_ROOT)/trust/xnat/scripts/xnat_db_drop_unused_dump.sh

# Once XNAT is confirmed on the new major: list the older cluster an upgrade kept for a rollback,
# and delete it with YES=1.
xnat-db-remove-old-cluster: ## List the PostgreSQL cluster an xnat-db major upgrade kept for a rollback; YES=1 deletes it
	@pod=$$($(KUBECTL) get pod $(XNAT_POD)=xnat-db -o name); \
	[ -n "$$pod" ] || { echo "❌ No xnat-db pod in $(NAMESPACE)."; exit 1; }; \
	$(KUBECTL) exec -i -n $(NAMESPACE) "$$pod" -c postgres -- sh -s -- $(if $(YES),--delete) \
	  < $(REPO_ROOT)/trust/xnat/scripts/xnat_db_remove_old_cluster.sh

# The node self-test's checklist against a deployed release (FLIP#1390): release ready, FL kit,
# API health, OMOP/Orthanc seeded, Orthanc auth, a real C-STORE, a dcm2niix conversion (FLIP#1448),
# FL client steady. Needs no hub.
# Writes selftest-k8s-<backend>-<stamp>.{json,md} under SELFTEST_OUT; exits non-zero on a failure.
SELFTEST_OUT ?= $(REPO_ROOT)/build/selftest
selftest: ## Run the trust self-test against the deployed release and write a report
	python3 $(CHART_DIR)/scripts/selftest_k8s.py --namespace $(NAMESPACE) --release $(RELEASE_NAME) \
		$(if $(KUBE_CONTEXT),--context $(KUBE_CONTEXT)) --out $(SELFTEST_OUT)

# NVFLARE's study file for job pods (FLIP#1678): the site maintains it, the fl-client only reads it. The
# fl-client builds the file (it holds the job-pod template and creates each project's images folder) and this
# replaces the ConfigMap the chart mounts. PROJECTS defaults to the projects whose approved cohort this trust
# holds, which is data-access-api's snapshot folder.
FL_NET ?= net-1
FL_STUDIES_POD = $(KUBECTL) get pod -n $(NAMESPACE) --field-selector=status.phase=Running -o name \
	-l app.kubernetes.io/instance=$(RELEASE_NAME),app.kubernetes.io/component
sync-project-studies: ## Give each approved project its NVFLARE study entry for job pods; PROJECTS="<id> ..." names them
	@set -e; \
	client=$$($(FL_STUDIES_POD)=fl-client,flip/network=$(FL_NET) | head -1); \
	[ -n "$$client" ] || { echo "❌ no running fl-client for $(FL_NET) in $(NAMESPACE)"; exit 1; }; \
	projects="$(PROJECTS)"; \
	if [ -z "$$projects" ]; then \
	  daa=$$($(FL_STUDIES_POD)=data-access-api | head -1); \
	  [ -n "$$daa" ] || { echo "❌ no running data-access-api to list the approved projects; pass PROJECTS=\"<id> ...\""; exit 1; }; \
	  projects=$$($(KUBECTL) exec -n $(NAMESPACE) "$$daa" -- sh -c 'cd "$${COHORT_SNAPSHOT_DIR:?}" && for f in */membership.json; do [ ! -f "$$f" ] || echo "$${f%%/*}"; done') \
	    || { echo "❌ cannot list the approved projects in data-access-api's COHORT_SNAPSHOT_DIR"; exit 1; }; \
	  [ -n "$$projects" ] || { echo "❌ no approved project found in data-access-api's COHORT_SNAPSHOT_DIR; pass PROJECTS=\"<id> ...\""; exit 1; }; \
	fi; \
	file=$$(mktemp); trap 'rm -f "$$file"' EXIT; \
	$(KUBECTL) exec -n $(NAMESPACE) "$$client" -c fl-client -- \
	  python -m flip.nvflare.project_studies /var/tmp/nvflare/workspace/local $$projects > "$$file"; \
	$(KUBECTL) create configmap $(RELEASE_NAME)-flip-trust-fl-studies-$(FL_NET) -n $(NAMESPACE) \
	  --from-file=study_runtime.yaml="$$file" --dry-run=client -o yaml | $(KUBECTL) apply -f -; \
	echo "✅ study entries for $$(echo $$projects | wc -w | tr -d ' ') project(s) on $(FL_NET)"

status: ## Show the status of the Helm release and run smoke tests
	@echo "=== Helm Release Status ==="
	$(HELM) status $(RELEASE_NAME) --namespace $(NAMESPACE)
	@echo ""
	@echo "=== Smoke Tests ==="
	@python3 $(CHART_DIR)/check_status.py --namespace $(NAMESPACE) --helm-release $(RELEASE_NAME) \
		$(if $(KUBE_CONTEXT),--context $(KUBE_CONTEXT))

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
		--set networkPolicies.allowedEgressCIDRsWithPorts[0].cidrs[0]=203.0.113.10/32 \
		--set networkPolicies.allowedEgressCIDRsWithPorts[0].port=8002 \
		--show-only templates/network-policy.yaml > /dev/null
	@echo "=== NetworkPolicies disabled ==="
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) $(VALUES_OPTS) \
		--set networkPolicies.enabled=false --show-only templates/network-policy.yaml || true
	@echo "✅ Egress NetworkPolicy variants render. See NETWORK-POLICY.md (#516)."

clean: ## Clean up rendered output
	@rm -f $(CHART_DIR)/*.rendered.yaml

xnat-init: ## Run the XNAT post-install init job (a no-op when xnat.enabled=false)
	@# One shell for the whole target: the "nothing to do" case must skip the wait too, and a
	@# bare `exit 0` in one recipe line would only end that line.
	@# Render first and look, because with `xnat.enabled=false` the chart renders no init Job at
	@# all (templates/xnat-init-job.yaml is guarded on both). `kubectl apply --selector` is then
	@# handed zero objects and fails with "no objects passed to apply", which turned a deploy of a
	@# trust that has no XNAT to configure into exit 3.
	@# The wait ends on Complete or Failed, whichever comes first: a Job past its backoffLimit never
	@# completes. Only a Failed condition fails it; a Failed-waiter that errors is dropped.
	@echo "▶  Rendering XNAT init job from chart templates..."
	@tmp=$$(mktemp); \
	trap 'rm -f "$$tmp"' EXIT; \
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$(VALUES_OPTS) \
		--set xnat.initJob.enabled=true > $$tmp \
		|| { echo "❌  could not render the chart — xnat-init not run."; exit 1; }; \
	if ! grep -q 'app.kubernetes.io/component: xnat-init' $$tmp; then \
		echo "⏭  XNAT is disabled (xnat.enabled=false) — nothing to initialise."; \
		exit 0; \
	fi; \
	$(KUBECTL) delete job -n $(NAMESPACE) --selector app.kubernetes.io/component=xnat-init \
		--ignore-not-found --cascade=foreground --timeout=$(HELM_TIMEOUT) || exit 1; \
	$(KUBECTL) apply -n $(NAMESPACE) -f $$tmp \
		--selector app.kubernetes.io/component=xnat-init || exit 1; \
	echo "⏳  Waiting for xnat-init job to complete (timeout $(HELM_TIMEOUT))..."; \
	init="--selector app.kubernetes.io/component=xnat-init --namespace $(NAMESPACE) --timeout=$(HELM_TIMEOUT)"; \
	$(KUBECTL) wait job $$init --for=condition=complete > /dev/null & complete=$$!; \
	$(KUBECTL) wait job $$init --for=condition=failed > /dev/null 2>&1 & failed=$$!; \
	job_failed=; \
	while kill -0 $$complete 2>/dev/null; do \
		if [ -n "$$failed" ] && ! kill -0 $$failed 2>/dev/null; then \
			if wait $$failed; then job_failed=1; break; fi; \
			failed=; \
		fi; \
		sleep 2; \
	done; \
	if [ -z "$$job_failed" ] && wait $$complete; then \
		[ -z "$$failed" ] || kill $$failed 2>/dev/null; \
		echo "✅  XNAT init complete."; \
	else \
		kill $$complete $$failed 2>/dev/null; \
		if [ -n "$$job_failed" ]; then reason="the Job failed"; else reason="it did not complete in $(HELM_TIMEOUT)"; fi; \
		echo "❌  XNAT init failed ($$reason) — check logs:"; \
		$(KUBECTL) logs -n $(NAMESPACE) -l app.kubernetes.io/component=xnat-init --tail=40; \
		exit 1; \
	fi

sync-kit-override: ## Regenerate the k8s-trust-<KIT>.yaml override from the kit (validates its governance document) without touching the cluster Secret
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make sync-kit-override KIT=Trust_K8s HUB=stag"; exit 1)
	@uv run --no-config $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--kit-file $(KIT_FILE) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR) \
		$(if $(NO_VOCAB_LOAD),--no-vocab-load) \
		$(KUBE_CONTEXT_ARG) \
		--no-apply-secret

patch-kit-secrets: ## Patch per-trust secrets from kit file into the cluster Secret WITHOUT overwriting the override YAML (safe after helm upgrade)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make patch-kit-secrets KIT=Trust_K8s HUB=stag"; exit 1)
	@uv run --no-config $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--kit-file $(KIT_FILE) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR) \
		$(if $(NO_VOCAB_LOAD),--no-vocab-load) \
		$(KUBE_CONTEXT_ARG) \
		--no-write-override
	@for svc in trust-api data-access-api imaging-api; do \
		$(KUBECTL) rollout restart deployment/$(RELEASE_NAME)-flip-trust-$$svc -n $(NAMESPACE); \
	done
	@for svc in trust-api data-access-api imaging-api; do \
		$(KUBECTL) rollout status deployment/$(RELEASE_NAME)-flip-trust-$$svc -n $(NAMESPACE) --timeout=120s; \
	done

# Registration is done ONCE on the hub, not here:
#   make new-trust TRUST_CODE=<KIT> TRUST_NAME="..."
#   make -C deploy/aws register-trusts KIT=<KIT> HUB=<env>
#   make sync-trust-kit KIT=<KIT> HUB=<env>           # fills the Hub-shared block
# This target then reads trust/.env.<KIT>.<env>, patches the per-trust keys into the
# chart's Kubernetes Secret, and writes a secret-free Helm override. Then deploy with
# `make up OVERRIDES_FILE=k8s-trust-<KIT>.yaml`. Env from $(HUB).
sync-kit: ## Sync a registered trust KIT into the cluster (patches Secret + writes k8s-trust-<KIT>.yaml override)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make sync-kit KIT=Trust_K8s HUB=stag"; exit 1)
	@uv run --no-config $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--kit-file $(KIT_FILE) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR) \
		$(if $(NO_VOCAB_LOAD),--no-vocab-load) \
		$(KUBE_CONTEXT_ARG)
