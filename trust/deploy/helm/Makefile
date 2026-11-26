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
CHART_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))
# REPO_ROOT is where trust/deploy/scripts/site_upgrade.py and the shared make fragments live.
REPO_ROOT := $(abspath $(CHART_DIR)/../../..)
# Kit-file env token (ENV) from HUB, derived once for every Makefile that reads HUB.
include $(REPO_ROOT)/deploy/env_mode.mk
# The trust kit this chart deployment is synced from (trust/.env.<CODE>.<env>, or an old
# name resolved by env_mode.mk's kit_file), handed to every script that reads it.
ifeq ($(origin KIT_FILE),undefined)
KIT_FILE := $(call kit_file,$(REPO_ROOT)/trust/,$(KIT))
endif
$(if $(KIT),$(call kit_hint,$(patsubst $(REPO_ROOT)/%,%,$(KIT_FILE))))
# The kit's FLIP_DATA_ROOT (FLIP#2113), read here because the kit path and VOCAB_DEST default under it.
# The chart's stores are PVCs, placed by the StorageClass (on k3s, under its --data-dir); the root
# only holds the two folders staged straight onto the node.
# It is the kit's, never the shell's: an exported root from another trust's kit must not leak in.
ifneq ($(origin FLIP_DATA_ROOT),command line)
ifneq ($(and $(KIT),$(filter stage-kit stage-vocab,$(MAKECMDGOALS)),$(if $(wildcard $(KIT_FILE)),,missing)),)
$(error ❌  KIT=$(KIT) names no kit file ($(KIT_FILE)): pass HUB=<stag|prod> for a hub kit)
endif
FLIP_DATA_ROOT := $(if $(KIT),$(shell sed -n 's/^FLIP_DATA_ROOT=\([^$(_HASH)]*\).*/\1/p' '$(KIT_FILE)' 2>/dev/null | tail -1))
endif
include $(REPO_ROOT)/deploy/data_root.mk

# The uid that must own the FL participant kit on the node (where it lives: KIT_PATH below).
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
# They must exist under the kit path before the pod starts: a subPath that is missing from the
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
# VOCAB_DEST the node directory omopDb.vocabLoad.hostPath names (the kit's OMOP_VOCAB_DIR):
# omop-vocab/ under the kit's FLIP_DATA_ROOT, else /opt/flip/omop-vocab.
# stage-vocab empties VOCAB_DEST first, as stage-kit does the kit path and with the same guards.
VOCAB_DEST ?= $(call data_root_or,omop-vocab,/opt/flip/omop-vocab)
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
VALUES_FILE ?= $(CHART_DIR)/values.yaml
SECRETS_FILE := $(wildcard $(CHART_DIR)/values-secrets.yaml)
OVERRIDES_FILE ?=

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
# Create the namespace only when it is missing: applying one that exists needs patch on the
# Namespace, which an account limited to that namespace does not have (FLIP#1986).
ENSURE_NAMESPACE = $(KUBECTL) get namespace $(NAMESPACE) >/dev/null 2>&1 || $(KUBECTL) create namespace $(NAMESPACE)

# What the "xnat-init did not complete" messages tell the operator to run. It carries no values:
# run bare, xnat-init renders the Job from the release's own (see the target). Only what selects
# the release is repeated (RELEASE_ARGS), so the command works as printed.
RELEASE_ARGS := $(strip $(if $(KUBE_CONTEXT),KUBE_CONTEXT=$(KUBE_CONTEXT)) $(foreach v,NAMESPACE RELEASE_NAME,$(if $(filter command environment,$(origin $(v))),$(v)=$($(v)))))
XNAT_INIT_RERUN := $(strip make -C trust/deploy/helm xnat-init $(RELEASE_ARGS))
XNAT_INIT_HELP := trust/deploy/helm/README.md, Running the XNAT init job by hand

# Determine which values files to pass
VALUES_OPTS := -f $(VALUES_FILE)
ifneq ($(strip $(SECRETS_FILE)),)
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

# Where the FL participant kit lives ON THE NODE, read from the kit file alone (FLIP#2042): fl-kit/ under
# its FLIP_DATA_ROOT (FLIP#2113), else its FL_KIT_DIR. `make sync-kit` renders the same path as
# flClient.kitHostPath (generate_values.kit_host_path) and stage-kit copies to it, so the two cannot
# disagree. Without KIT= (a dev kind cluster, a bare render), and for a kit that sets neither, it is the
# canonical path every shipped kit carries (generate_values.DEFAULT_KIT_HOST_PATH).
# FL_KIT_DIR is read as sync_k8s_kit.read_env_vars reads it (last line wins, quotes dropped): tests/test_stage_kit.py.
DEFAULT_KIT_PATH := /opt/flip/fl-kit
KIT_PATH := $(if $(FLIP_DATA_ROOT),$(FLIP_DATA_ROOT)/fl-kit,$(or $(if $(KIT),$(strip $(shell sed -n 's/^FL_KIT_DIR=//p' '$(KIT_FILE)' 2>/dev/null | tail -1 | tr -d "\"'"))),$(DEFAULT_KIT_PATH)))
ifneq ($(filter command environment override,$(origin KIT_DEST) $(origin FL_CLIENT_KIT_HOST_PATH)),)
$(error ❌  KIT_DEST and FL_CLIENT_KIT_HOST_PATH are gone (FLIP#2042): the kit's path on the node is FL_KIT_DIR in the kit file (or fl-kit/ under its FLIP_DATA_ROOT). Set it there and pass KIT=<CODE>)
endif
ifneq ($(filter stage-kit,$(MAKECMDGOALS)),)
# stage-kit empties the path before copying, so it must be a directory of its own: a relative one
# would be wiped under the node's working directory, and / is the node's root filesystem.
ifeq ($(filter-out /,$(filter /%,$(KIT_PATH))),)
$(error ❌  FL_KIT_DIR in $(KIT_FILE) is '$(KIT_PATH)': it must be an absolute path other than / (stage-kit clears it before copying))
endif
endif
# The chart requires flClient.kitHostPath, and an override file (sync-kit's always) carries it. A
# render with none (make test, a plain `make template`, `make deploy [KIT=]`) gets KIT_PATH here,
# the same path CHECK_KIT_PATH compares against.
ifeq ($(strip $(OVERRIDES_FILE)),)
	VALUES_OPTS := $(VALUES_OPTS) --set flClient.kitHostPath=$(KIT_PATH)
endif
# Before `helm upgrade`: a release that mounts another path than the one this deploy renders is
# refused (scripts/check_kit_path.py). Only where that path is known here: a kit, or no override.
ifneq ($(or $(KIT),$(if $(strip $(OVERRIDES_FILE)),,bare)),)
CHECK_KIT_PATH = $(HELM) get values $(RELEASE_NAME) --namespace $(NAMESPACE) -o json 2>/dev/null \
	| python3 $(CHART_DIR)/scripts/check_kit_path.py --kit-path "$(KIT_PATH)" \
	  --kit-file "$(if $(KIT),$(KIT_FILE))" $(if $(KIT_MOVED),--moved)
else
CHECK_KIT_PATH = true
endif
# A re-stage must reach the path a live release mounts, or the FL client keeps the old credentials
# with nothing failing (e.g. a FLIP_DATA_ROOT added to its kit, FLIP#2113). stage-kit empties its
# target, so it never takes that path from the cluster: when the two differ it stops, and the operator
# either makes the kit file say the mounted path or passes KIT_MOVED=1 here and at the next deploy.
ifneq ($(and $(filter stage-kit,$(MAKECMDGOALS)),$(KUBE_CONTEXT)),)
ifeq ($(KIT_MOVED),)
RELEASE_KIT_PATH := $(shell $(HELM) get values $(RELEASE_NAME) --namespace $(NAMESPACE) -o json 2>/dev/null \
	| python3 $(CHART_DIR)/scripts/check_kit_path.py --print-mounted)
ifneq ($(if $(RELEASE_KIT_PATH),$(filter-out $(KIT_PATH),$(RELEASE_KIT_PATH))),)
$(error ❌  release $(RELEASE_NAME) mounts its kit at $(RELEASE_KIT_PATH), and the kit file puts it at $(KIT_PATH) (fl-kit/ under FLIP_DATA_ROOT, else FL_KIT_DIR): make $(KIT_FILE) say $(RELEASE_KIT_PATH) to re-stage it in place, or pass KIT_MOVED=1 here and at the next deploy to move it)
endif
endif
endif

# The release pin (FLIP#1204): TAG=vX.Y.Z sets global.image.tag, which every FLIP-built
# image follows (templates/_helpers.tpl flip-trust.imageTag). Last so it wins over the
# override file's value, which sync-kit fills from the kit's DOCKER_TAG.
TAG ?=
ifneq ($(strip $(TAG)),)
	VALUES_OPTS := $(VALUES_OPTS) --set global.image.tag=$(TAG)
endif

.PHONY: sync-project-studies help lint template deploy deploy-release failing-hooks undeploy up down test validate clean deploy-trust-k8s undeploy-trust-k8s upgrade-trust-k8s dry-run update-deps sync-kit sync-kit-override patch-kit-secrets xnat-init reseed _reseed-job kit-secret stage-kit download-vocab stage-vocab preflight template-egress-variants smoke-cstore selftest xnat-db-migrate-dump xnat-db-remove-old-cluster

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
	 CHART_KINDS="$$($(HELM) template $(RELEASE_NAME) $(CHART_DIR) --namespace $(NAMESPACE) $(VALUES_OPTS) 2>/dev/null | sed -n 's/^kind: //p' | sort -u | tr '\n' ' ')" \
	 bash $(CHART_DIR)/scripts/preflight.sh

kit-secret: ## Put this trust's FL kit in a Secret for flClient.kit.source=secret (managed node pools, e.g. AKS)
	@[ -n "$(KIT_SRC)" ] || { echo "❌ KIT_SRC=<the slot's kit directory: local/ startup/ … or certificates/ keys/> is required"; exit 1; }
	$(ENSURE_NAMESPACE)
	python3 $(CHART_DIR)/scripts/kit_secret.py --kit-src $(KIT_SRC) \
		--name $(RELEASE_NAME)-flip-trust-fl-kit --namespace $(NAMESPACE) | $(KUBECTL) apply -f -

stage-kit: ## Place this trust's FL participant kit onto the node (run BEFORE deploy)
	## The chart never fetches the kit — a trust holds no FLIP AWS credentials, and the
	## kit arrives out-of-band (trust/README.md). This copies an already-obtained kit
	## from KIT_SRC onto the node at the path the chart mounts (flClient.kitHostPath).
	##
	##   make stage-kit KIT=<CODE> HUB=<env> KIT_SRC=~/kits/Trust_2 KUBE_CONTEXT=kind-flip-kch
	##
	## The destination is the kit file's path for it (pass KIT=<CODE>): fl-kit/ under its
	## FLIP_DATA_ROOT, else its FL_KIT_DIR, which `make sync-kit` renders as flClient.kitHostPath;
	## without KIT it is /opt/flip/fl-kit.
	## KIT_SRC contents are the slot's own kit: for FLARE local/ startup/ transfer/,
	## for Flower certificates/ and keys/ holding THIS slot's credential only. That
	## shape is also how the owning uid is chosen (KIT_UID above) — no FL_BACKEND
	## needed; a kit with neither or both shapes is refused before anything is copied.
	## scripts/stage-to-node.sh does the copy (FLIP#1958): on a kind node with docker exec/cp, on
	## any other node (k3s, a VM) through a short-lived root pod pinned to it, with the kit path as a
	## hostPath and kubectl cp. A namespace under Pod Security "baseline" refuses that pod and the
	## chart's own kit hostPath alike: use the kit Secret route there (flClient.kit.source=secret,
	## `make kit-secret`).
	##
	## The path's previous contents are replaced, not merged, for the same reason the EC2 play
	## wipes before its sync: re-staging after a kit-date bump, a backend switch, or a corrected
	## over-broad sync would otherwise leave credentials that should no longer be on the node.
	## The mounted subdirectories are created and the whole kit is made private to KIT_UID.
	##
	## With several nodes, NODE=<name> is required: the node the FL client runs on, which is
	## imaging-api's unless flClient.affinity says otherwise. One node needs no NODE.
	@[ -n "$(KUBE_CONTEXT)" ] || (echo "❌  KUBE_CONTEXT is required — this writes to a node's filesystem, and defaulting to kubectl's current context ($$(kubectl config current-context 2>/dev/null || echo none)) would stage into whichever cluster that happens to be. e.g. KUBE_CONTEXT=kind-flip-kch"; exit 1)
	@echo "📦 Staging $(KIT_SRC) ($(KIT_BACKEND) kit, owner uid $(KIT_UID)) → [$(KUBE_CONTEXT)] $(KIT_PATH)"
	NAMESPACE=$(NAMESPACE) $(CHART_DIR)/scripts/stage-to-node.sh --context "$(KUBE_CONTEXT)" \
	  --src "$(KIT_SRC)" --dest "$(KIT_PATH)" --subdirs "$(KIT_SUBDIRS)" --owner $(KIT_UID) $(if $(NODE),--node "$(NODE)")
	@echo "✅ Kit staged at $(KIT_PATH), the path a deploy$(if $(KIT), of $(KIT)) mounts."

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
	$(ENSURE_NAMESPACE)
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) --namespace $(NAMESPACE) $(VALUES_OPTS) | \
		NAMESPACE=$(NAMESPACE) RELEASE_NAME=$(RELEASE_NAME) KUBECTL="$(KUBECTL)" $(CHART_DIR)/scripts/migrate-strategy.sh
	@$(CHECK_KIT_PATH)
	ready=$$(python3 $(CHART_DIR)/scripts/rollout_wait.py --report-ready \
		--namespace $(NAMESPACE) --release $(RELEASE_NAME) --kubectl "$(KUBECTL)"); \
	$(HELM) upgrade --install $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--timeout $(HELM_TIMEOUT) \
		--set xnat.initJob.enabled=false \
		$(VALUES_OPTS) \
		|| { $(SUBMAKE) --no-print-directory failing-hooks; exit 1; }; \
	python3 $(CHART_DIR)/scripts/rollout_wait.py \
		--namespace $(NAMESPACE) --release $(RELEASE_NAME) \
		--timeout $(HELM_TIMEOUT) --kubectl "$(KUBECTL)" --ready-before "$$ready" \
		|| { $(SUBMAKE) --no-print-directory failing-hooks; exit 1; }

# The two steps are separate targets so that WHICH one failed is the signal. GNU make exits 2 for
# any failed recipe whatever code the recipe returned, so an exit code cannot carry "upgraded, but
# XNAT not configured" through make (FLIP#1645). A caller that must tell the two apart (the
# auto-upgrade agent, FLIP#1640; deploy-trust-k8s; upgrade-trust-k8s) runs deploy-release, then
# xnat-init, and reads which command failed. `deploy` is the two in sequence, for humans.
deploy: deploy-release ## Deploy the Helm chart to the cluster (deploy-release, then xnat-init)
	@$(SUBMAKE) --no-print-directory xnat-init || { \
		printf '\033[0;33m%s\033[0m\n' \
			"⚠️  the release is live and rolled out, but xnat-init did not complete (see $(XNAT_INIT_HELP))." \
			"⚠️  Re-run: $(XNAT_INIT_RERUN) (raise HELM_TIMEOUT if it just needed longer)."; \
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
		echo "⚠️  the release is live and rolled out, but xnat-init did not complete (see $(XNAT_INIT_HELP)). Patching per-trust secrets, then failing. Afterwards re-run: $(XNAT_INIT_RERUN)"; \
	elif [ $$rc -ne 0 ]; then \
		echo "❌ the release did not land (helm or the rollout wait failed). Patching per-trust secrets first so trust-api gets the correct key and does not poll with the stale seed key (#595), then failing."; \
	fi; \
	$(MAKE) patch-kit-secrets KIT=$(KIT) || { \
		if [ $$rc -eq 0 ] || [ $$step = xnat-init ]; then \
			echo "❌ patch-kit-secrets failed: the trust may not be running on the kit's keys. Re-run: $(strip make -C trust/deploy/helm patch-kit-secrets KIT=$(KIT) $(if $(HUB),HUB=$(HUB)) $(RELEASE_ARGS))"; \
		fi; rc=1; }; \
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
	$(MAKE) sync-kit KIT=$(KIT) NO_NEXT_STEP=1
	$(MAKE) xnat-db-migrate-dump
	$(MAKE) deploy-release OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml TAG=$(shell sed -n 's/^DOCKER_TAG=//p' $(KIT_FILE) | tail -1) \
	  || { $(MAKE) xnat-db-drop-unused-dump AFTER_FAILED_DEPLOY=1; exit 1; }
	$(MAKE) xnat-db-drop-unused-dump
	$(MAKE) xnat-init OVERRIDES_FILE=$(CHART_DIR)/k8s-trust-$(KIT).yaml TAG=$(shell sed -n 's/^DOCKER_TAG=//p' $(KIT_FILE) | tail -1) \
	  || { echo "⚠️  the release is live and rolled out, but xnat-init did not complete (see $(XNAT_INIT_HELP)). Re-run: $(XNAT_INIT_RERUN)"; exit 1; }
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
sync-project-studies: ## Not needed since v0.16 (job pods build each project's study entry at launch): write the operator's study file
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

# $(VALUES_OPTS) for a render that starts from a release's own values: without its leading
# `-f <chart values.yaml>`, which laid over them would reset every key to the chart default (Helm
# starts from that file anyway). A VALUES_FILE the caller named is kept.
VALUES_OPTS_OVER_RELEASE = $(if $(filter command environment,$(origin VALUES_FILE)),$(VALUES_OPTS),$(wordlist 3,$(words $(VALUES_OPTS)),$(VALUES_OPTS)))

# Run one of the chart's hook Jobs on its own, outside a helm upgrade: render the chart with the
# extra --set $(2), and when it renders a Job labelled app.kubernetes.io/component=$(1), replace any
# earlier run of it and wait. $(3) names it in the output; $(4) is what to say when the chart renders
# none (the Job's own guard is off), which is not an error.
# One shell for the whole recipe: the "nothing to do" case must skip the wait too, and a bare
# `exit 0` in one recipe line would only end that line.
# Handed no override file (the re-run the failure messages print, or KIT= alone, which names none),
# the Job is rendered from the values the release was deployed with: from the chart defaults
# xnat-init would configure XNAT for the mocked PACS on the default image tag and report success
# (FLIP#2047). Whatever else the caller passed (VALUES_FILE, PLATFORM_VALUES, TAG) is applied over
# them. A release that cannot be read, or does not exist, stops it here: rendered without one, the
# Job waits until HELM_TIMEOUT on a Secret no release created.
# Render first and look, because a guarded-off Job is not rendered at all: `kubectl apply --selector`
# is then handed zero objects and fails with "no objects passed to apply", which turned a deploy of
# a trust that has no XNAT to configure into exit 3.
# The wait ends on Complete or Failed, whichever comes first: a Job past its backoffLimit never
# completes. Only a Failed condition fails it; a Failed-waiter that errors is dropped.
define run_chart_job
	@echo "▶  Rendering the $(3) job from chart templates..."
	@tmp=$$(mktemp); vals=$$(mktemp); \
	trap 'rm -f "$$tmp" "$$vals"' EXIT; \
	opts="$(VALUES_OPTS)"; \
	if [ -z "$(strip $(OVERRIDES_FILE))" ]; then \
		$(HELM) get values $(RELEASE_NAME) --namespace $(NAMESPACE) -o yaml > $$vals \
			|| { echo "❌  no release $(RELEASE_NAME) in $(NAMESPACE) — pass RELEASE_NAME/NAMESPACE."; exit 1; }; \
		opts="-f $$vals $(VALUES_OPTS_OVER_RELEASE)"; \
		echo "   with the values release $(RELEASE_NAME) was deployed with."; \
	fi; \
	$(HELM) template $(RELEASE_NAME) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$$opts \
		--set $(2) > $$tmp \
		|| { echo "❌  could not render the chart — $(3) not run."; exit 1; }; \
	if ! grep -q 'app.kubernetes.io/component: $(1)' $$tmp; then \
		echo "⏭  $(4)"; \
		exit 0; \
	fi; \
	$(KUBECTL) delete job -n $(NAMESPACE) --selector app.kubernetes.io/component=$(1) \
		--ignore-not-found --cascade=foreground --timeout=$(HELM_TIMEOUT) || exit 1; \
	$(KUBECTL) apply -n $(NAMESPACE) -f $$tmp \
		--selector app.kubernetes.io/component=$(1) || exit 1; \
	echo "⏳  Waiting for the $(3) job to complete (timeout $(HELM_TIMEOUT))..."; \
	job="--selector app.kubernetes.io/component=$(1) --namespace $(NAMESPACE) --timeout=$(HELM_TIMEOUT)"; \
	$(KUBECTL) wait job $$job --for=condition=complete > /dev/null & complete=$$!; \
	$(KUBECTL) wait job $$job --for=condition=failed > /dev/null 2>&1 & failed=$$!; \
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
		echo "✅  $(3) complete."; \
	else \
		kill $$complete $$failed 2>/dev/null; \
		if [ -n "$$job_failed" ]; then reason="the Job failed"; else reason="it did not complete in $(HELM_TIMEOUT)"; fi; \
		echo "❌  $(3) failed ($$reason) — check logs:"; \
		$(KUBECTL) logs -n $(NAMESPACE) -l app.kubernetes.io/component=$(1) --tail=40; \
		exit 1; \
	fi
endef

xnat-init: ## Run the XNAT post-install init job (a no-op when xnat.enabled=false)
	$(call run_chart_job,xnat-init,xnat.initJob.enabled=true,XNAT init,XNAT is disabled (xnat.enabled=false) — nothing to initialise.)

# The trust-seed hook seeds only an empty store (FLIP#2111), so neither an upgrade nor a
# trustData.version bump reaches a seeded trust. This runs it once with trustData.seed.reseed=true:
# OMOP's listed projects reloaded, Orthanc's cleared and re-uploaded, at the release's version.
# KIT is required, and its k8s-trust-<KIT>.yaml with it (OVERRIDES_FILE= names another): the
# partition (trustNumber, trustData.seed.sourceTrust) comes from that override, and without it the
# Job would load slot 1's data into any trust.
reseed: ## Re-seed a seeded trust's sample data at its trustData.version (after a pin bump); needs KIT
	@[ -n "$(KIT)" ] || { echo "❌ KIT=<CODE> is required: the re-seed loads that trust's partition, from its override"; exit 1; }
	@[ -f "$(RESEED_OVERRIDES)" ] || { echo "❌ $(RESEED_OVERRIDES) not found — run make sync-kit KIT=$(KIT) first"; exit 1; }
	@$(SUBMAKE) --no-print-directory _reseed-job KIT=$(KIT) HUB=$(HUB) OVERRIDES_FILE=$(RESEED_OVERRIDES)
RESEED_OVERRIDES = $(or $(OVERRIDES_FILE),$(CHART_DIR)/k8s-trust-$(KIT).yaml)

_reseed-job:
	$(call run_chart_job,trust-seed,trustData.seed.reseed=true,Trust re-seed,Seeding is off for this release (trustData.seed) — nothing to re-seed.)

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
		--no-apply-secret \
		--no-next-step

patch-kit-secrets: ## Patch per-trust secrets from kit file into the cluster Secret WITHOUT overwriting the override YAML (safe after helm upgrade)
	@[ -n "$(KIT)" ] || (echo "❌  KIT is required. Usage: make patch-kit-secrets KIT=Trust_K8s HUB=stag"; exit 1)
	@# The three APIs restart on every run. The FL clients read the trust-internal service key and its
	@# header name from the same Secret, but a restart ends an in-flight FL job and this target runs
	@# on every deploy-trust-k8s, so a client restarts only when the Secret no longer holds what it
	@# was last restarted with (FLIP#2047). That is a sha256 of the two entries, kept as an annotation
	@# on the client's Deployment (its own metadata: on the pod template it would roll the pods, and
	@# Helm does not template it, so an upgrade leaves it). It holds whichever verb patched the
	@# Secret: sync-kit patches it and restarts nothing. A client with nothing recorded yet restarts
	@# only if this run changed the Secret. "pending" is written before a restart, so a run that
	@# failed there is repaired by the next. A Secret that cannot be read stops the target; only
	@# NotFound, before the patch, is "no Secret yet". The entries are only ever piped into the hash.
	@# Deployments are listed as <component>=<name>=<ready replicas>=<recorded>; one the release does
	@# not render is not restarted, and a client that was not ready is not waited on: awaiting its
	@# kit or a free GPU it stays Pending, and the wait could only time out (scripts/rollout_wait.py).
	@# No trust API at all means the wrong release or namespace, whose Secret the patch just created.
	@secret=$(RELEASE_NAME)-flip-trust-secrets; mark=flip/internal-service-key-sha256; \
	fingerprint() { \
		v=$$($(KUBECTL) get secret $$secret -n $(NAMESPACE) --ignore-not-found \
			-o jsonpath='{.data.trust-internal-service-key}:{.data.trust-internal-service-key-header}') || return 1; \
		[ -n "$$v" ] || return 0; \
		printf '%s' "$$v" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'; \
	}; \
	before=$$(fingerprint) || { echo "❌  could not read $$secret — nothing patched or restarted."; exit 1; }; \
	uv run --no-config $(CHART_DIR)/sync_k8s_kit.py \
		--kit $(KIT) \
		--env $(ENV) \
		--kit-file $(KIT_FILE) \
		--namespace $(NAMESPACE) \
		--secret-name $(RELEASE_NAME)-flip-trust-secrets \
		--release-name $(RELEASE_NAME) \
		--output-dir $(CHART_DIR) \
		$(if $(NO_VOCAB_LOAD),--no-vocab-load) \
		$(KUBE_CONTEXT_ARG) \
		--no-write-override || exit 1; \
	now=$$(fingerprint) && [ -n "$$now" ] || { echo "❌  could not read $$secret after the patch — nothing restarted."; exit 1; }; \
	deployments=$$($(KUBECTL) get deployment -n $(NAMESPACE) -l app.kubernetes.io/instance=$(RELEASE_NAME) \
		-o jsonpath='{range .items[*]}{.metadata.labels.app\.kubernetes\.io/component}={.metadata.name}={.status.readyReplicas}={.metadata.annotations.flip/internal-service-key-sha256}{" "}{end}') || exit 1; \
	apis=; clients=; record=; wait=; \
	for d in $$deployments; do \
		component=$${d%%=*}; d=$${d#*=}; name=$${d%%=*}; d=$${d#*=}; ready=$${d%%=*}; recorded=$${d#*=}; \
		case "$$component" in \
		trust-api|data-access-api|imaging-api) apis="$$apis $$name"; wait="$$wait $$name" ;; \
		fl-client) \
			if [ "$$recorded" = "$$now" ] || { [ -z "$$recorded" ] && [ "$$before" = "$$now" ]; }; then \
				echo "ⓘ  $$name: internal service key unchanged: FL client left running"; \
				[ -n "$$recorded" ] || record="$$record $$name"; \
			else \
				echo "🔄 $$name: internal service key changed: restarting the FL client"; \
				clients="$$clients $$name"; \
				case "$$ready" in ""|0) ;; *) wait="$$wait $$name" ;; esac; \
			fi ;; \
		esac; \
	done; \
	[ -n "$$apis" ] || { echo "❌  no trust API Deployment for release $(RELEASE_NAME) in $(NAMESPACE) — pass RELEASE_NAME/NAMESPACE."; exit 1; }; \
	for name in $$apis; do \
		$(KUBECTL) rollout restart deployment/$$name -n $(NAMESPACE) || exit 1; \
	done; \
	for name in $$clients; do \
		$(KUBECTL) annotate deployment/$$name -n $(NAMESPACE) --overwrite $$mark=pending > /dev/null \
		&& $(KUBECTL) rollout restart deployment/$$name -n $(NAMESPACE) || exit 1; \
	done; \
	for name in $$clients $$record; do \
		$(KUBECTL) annotate deployment/$$name -n $(NAMESPACE) --overwrite $$mark=$$now > /dev/null || exit 1; \
	done; \
	for name in $$wait; do \
		$(KUBECTL) rollout status deployment/$$name -n $(NAMESPACE) --timeout=120s || exit 1; \
	done

# Registration is done ONCE on the hub, not here:
#   make new-trust TRUST_CODE=<KIT> TRUST_NAME="..."
#   make -C deploy/aws register-trusts KIT=<KIT> HUB=<env>
#   make sync-trust-kit KIT=<KIT> HUB=<env>           # fills the Hub-shared block
# This target then reads trust/.env.<KIT>.<env>, patches the per-trust keys into the
# chart's Kubernetes Secret, and writes a secret-free Helm override. Then deploy with
# `make deploy-trust-k8s KIT=<KIT> HUB=<env>`, which keeps the kit's platform values. Env from $(HUB).
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
		$(if $(NO_NEXT_STEP),--no-next-step) \
		$(KUBE_CONTEXT_ARG)
