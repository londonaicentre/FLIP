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

.PHONY: build up down up-xnat down-xnat xnat-shell xnat-stack-down clean \
	xnat-war-download xnat-plugins-download xnat-reset xnat-configure \
	create-xnat-network test unit_test local_test

# Mirrors trust/Makefile: no hub `.env.*` include. Runtime config (DOCKER_REGISTRY,
# DOCKER_TAG, XNAT_PORT, XNAT_ADMIN_*, XNAT_SERVICE_*, XNAT_DATABASE_URL) and the
# XNAT stack passwords (XNAT_DATASOURCE_PASSWORD, XNAT_DATASOURCE_ADMIN_PASSWORD,
# XNAT_ACTIVEMQ_PASSWORD — minted by `make register-trust`) are pulled from the
# kit file via `-include $(KIT_FILE)` below; non-secret XNAT-internal config
# (XNAT_VERSION, datasource driver/URL/usernames, XNAT_SMTP_*, heap, paths) comes
# from trust/xnat/.env. Passwords are runtime env only — never build args. The dev-admin build path (xnat-war-download /
# xnat-plugins-download) needs FLIP_ARTIFACTS_BUCKET_NAME — pass it on the
# CLI if invoking those targets directly.

# Load XNAT-specific .env file
ifneq ("$(wildcard .env)","")
include .env
export $(shell sed 's/=.*//' .env)
endif

# Default DOCKER_TAG to 'stag' when running standalone (not via root Makefile which exports its own)
DOCKER_TAG ?= stag
export DOCKER_TAG

# XNAT images often lag the rest of the stack — branch builds for trust-api /
# imaging-api / data-access-api / orthanc are cheap (small images, fast builds)
# but the XNAT images are large and slow to rebuild for ad-hoc branches.
# XNAT_TAG lets a deploy pin the XNAT layer at a known-good tag (e.g. stag)
# while the rest of the stack runs a feature-branch tag via DOCKER_TAG.
# Defaults to DOCKER_TAG so existing single-tag deploys are unchanged.
XNAT_TAG ?= ${DOCKER_TAG}
export XNAT_TAG

# XNAT_DATA_DIR is set AFTER the kit-file include below so the kit's value
# wins over the make-side default.

# ── Per-trust XNAT stack ──────────────────────────────────────────────────
# KIT selects the trust — now the trust CODE; the kit file is
# trust/.env.<CODE>.<env> (ENV derived from PROD, mirroring trust/Makefile),
# with a legacy fallback to trust/.env.<KIT> for the not-yet-migrated
# EC2/on-prem path (KIT=<slot>, no env suffix). That kit file carries this
# trust's XNAT_PORT / PACS_UI_PORT (unsuffixed) and its hub-assigned
# FL_KIT_SLOT_NUMBER — the same host-local profile trust/Makefile reads. KIT
# has no default so non-KIT targets (build, test) don't spuriously -include a
# file. The XNAT swarm stack name and overlay network are derived from the
# assigned slot NUMBER (not the CODE), so XNAT co-resides on the same
# deploy_trust-network-<N> as the slot-numbered trust core services.
ifeq ($(PROD),true)
ENV := production
else ifeq ($(PROD),stag)
ENV := stag
else
ENV := development
endif
KIT_FILE := $(if $(wildcard ../.env.$(KIT).$(ENV)),../.env.$(KIT).$(ENV),../.env.$(KIT))
-include $(KIT_FILE)
# Kit-file values must reach the shell env, not just Make vars. docker stack
# deploy (unlike docker compose) has no --env-file flag; it substitutes
# ${XNAT_ADMIN_USER}, ${PACS_DICOM_PORT}, ${DOCKER_REGISTRY}, etc. in the
# compose YAML from the calling shell env only. Without this export, the
# stack came up with empty admin creds and configure-xnat.sh would 401/403
# on every auth call, leaving "Site has not yet been configured" forever.
# Pattern mirrors line 28 for trust/xnat/.env; Make silently ignores the
# comment-line "exports" that sed leaks through.
ifneq ("$(wildcard $(KIT_FILE))","")
export $(shell sed 's/=.*//' $(KIT_FILE))
endif

# INSTANCE_PREFIX for the per-instance trust network. FLIP_INSTANCE arrives already exported
# by ../../deploy/instance.mk when trust/Makefile drives this sub-make; on the standalone
# `make -C trust/xnat ...` path it must come from the environment, which
# check_network_matches_trust below verifies rather than trusts — an unprefixed XNAT beside a
# prefixed trust deploys healthily and simply cannot reach imaging-api.
include ../../deploy/instance.mk
# Slot NUMBER the hub assigned this trust, read from the kit file — NOT a
# substring of the CODE. Drives the stack name (xnat<N>) and overlay network
# ($(INSTANCE_PREFIX)deploy_trust-network-<N>); trust/Makefile derives TRUST_NUM from
# FL_KIT_SLOT_NUMBER identically, so XNAT and the trust core services land on
# the same network.
TRUST_NUM := $(FL_KIT_SLOT_NUMBER)
# Instance-scoped like TRUST_PROJECT: swarm stack names are global to the daemon, so two
# hubs whose slot numbering both restart at 1 would otherwise deploy over each other's XNAT.
XNAT_STACK := $(INSTANCE_PREFIX)xnat$(TRUST_NUM)
XNAT_NETWORK := $(INSTANCE_PREFIX)deploy_trust-network-$(TRUST_NUM)
# The trust core services this XNAT must sit beside — same derivation as trust/Makefile's
# TRUST_PROJECT. Instance-scoped: unprefixed, the guard below inspects whichever hub owns
# plain `trust<N>` and reports on a trust that is not ours.
TRUST_PROJECT := $(INSTANCE_PREFIX)trust$(TRUST_NUM)
# Defaults are the Trust_1 allocation, so a single-trust host (EC2 / on-prem)
# needs no XNAT port entries in its kit file.
XNAT_PORT_EFFECTIVE := $(or $(XNAT_PORT),8104)
PACS_UI_PORT_EFFECTIVE := $(or $(PACS_UI_PORT),8042)

# Host path that backs this trust's XNAT bind mounts (parent of xnat-data/
# and xnat-db-data/). Sourced from the kit file's Host-local profile;
# falls back to a per-trust default when the kit omits it:
#   stag/prod → /opt/flip/xnat-trust$(TRUST_NUM)   (per-slot dir; isolates
#               xnat1 / xnat2 when multiple trusts share one EC2 host)
#   dev       → ./xnat-data-trust$(TRUST_NUM)   (per-stack local dir; isolates
#               concurrent Trust_1 / Trust_2 stacks on the same host)
# Override per-host (e.g. on a box where /opt is small and the XNAT archive
# lives on a larger /data volume) by setting XNAT_DATA_DIR in
# trust/.env.<KIT> or exporting it before invoking make. Resolved to an
# absolute path so docker-compose-stack.{development,production}.yml can
# bind-mount it directly without a separate `${XNAT_PATH}/` prefix. Both
# this recipe and the compose stacks read the same exported var so the
# dirs Make creates and the dirs compose mounts stay in lockstep.
XNAT_DATA_DIR ?= $(if $(filter true stag,$(PROD)),/opt/flip/xnat-trust$(TRUST_NUM),./xnat-data-trust$(TRUST_NUM))
XNAT_DATA_DIR := $(abspath $(XNAT_DATA_DIR))
export XNAT_DATA_DIR

# uid/gid every XNAT bind-mount source must be owned by. `xnat-web` runs as the
# in-image `xnat` user, created with a fixed id in trust/xnat/xnat/Dockerfile
# (`useradd --system --uid 1001 --gid xnat`). That is a property of the image,
# not of the host, so it can neither be derived from $(USER) nor assumed to be
# the conventional 1000 that the FLIP-built Python services use.
#
# XNAT ingests DICOM into archive/, stages it through build/ and cache/, and
# rotates its logs into tomcat_logs/. If any of those is not writable it accepts
# the inbound association, fails the write, and aborts it — so the PACS reports a
# *network* fault ("Peer aborted Association"), pointing at ports and AE titles
# rather than at permissions. The same failure stops XNAT writing the application
# logs that would name the real cause, so the evidence goes with it (FLIP#1095).
#
# xnat-db is unaffected, but not merely because it runs as root: trust/xnat/postgres/
# Dockerfile sets no USER and the compose pins none, so the container starts as root
# and the stock postgres entrypoint re-chowns PGDATA and drops to `postgres` itself.
# That is what makes a 1001-owned xnat-db-data harmless, and it is what would go away
# if someone pinned a `user:` on xnat-db. Its dir is included on stag/prod only, so
# the whole tree carries one owner.
#
# deploy/providers/AWS/site.yml and deploy/providers/local/site_local_trust.yml
# provision XNAT's bind mounts with this same id: the uid is the invariant all three
# paths share, and the one to keep in step. The directories deliberately are not —
# both playbooks provision /opt/flip/xnat, while stag/prod resolves XNAT_DATA_DIR to
# the per-slot /opt/flip/xnat-trust$(TRUST_NUM) above so two trusts sharing an EC2
# host stay isolated. Compose and xnat-reset read that same variable, so this recipe
# provisions the tree XNAT actually writes to and the playbooks' one is not it — an
# ownership change made there would look right and reach nothing.
XNAT_CONTAINER_UID := 1001
XNAT_CONTAINER_GID := 1001
XNAT_DIR_OWNER := $(XNAT_CONTAINER_UID):$(XNAT_CONTAINER_GID)

# The bind-mount sources xnat-reset provisions. xnat-db-data is stag/prod-only:
# the development stack gives xnat-db no persistent bind mount.
XNAT_DATA_DIRS := $(addprefix $(XNAT_DATA_DIR)/xnat-data/,tomcat_logs archive build cache) \
                  $(if $(filter true stag,$(PROD)),$(XNAT_DATA_DIR)/xnat-db-data)

# Verify every bind-mount source is owned by the container uid, and fail naming
# the exact remedy when it is not. Fatal rather than a warning because the
# symptom surfaces layers away from the cause (see above) and a warning is easy
# to lose in long bring-up output — the same reasoning, and the same shape, as
# $(ensure_net_dirs) in trust/Makefile. Checked after the chown rather than
# instead of it as defence in depth, not because the chown itself might be
# refused: all three branches chown under sudo, and `set -e` aborts on one that
# fails outright before the guard is reached. What is left for the guard is a
# chown that did not cover what it was meant to — it applies recursively to
# $(XNAT_DATA_DIR) while the guard stats each entry of $(XNAT_DATA_DIRS), so those
# two drifting apart, or a later edit narrowing or dropping the chown on one
# branch, surfaces here rather than at the first DICOM write.
# Counting matches (rather than testing the chown's exit status) also catches a
# dir that is missing entirely, since stat then prints nothing for it.
#
# $(1) is a command printing "<uid>:<gid>" per source, one line each — run
# locally, or over ssh when the swarm host is remote. $(2) is the prefix that
# makes the printed remedy runnable where the directories actually are: empty on
# the local branches, `ssh <ctx>` on the remote-swarm one, where the paths live
# on the trust host and an operator pasting a bare `sudo chown` would either get
# "no such file or directory" or chown a same-named path on their own machine.
#
# stat's stderr is deliberately not discarded. A stat that fails for a reason
# other than a missing directory — an unreadable parent, or a non-GNU stat that
# rejects `-c` — otherwise counts zero and is reported as wrong ownership, which
# sends the reader after the wrong fault. A missing directory still counts zero
# and still fires the guard, now with stat naming the path it could not read.
define check_xnat_dir_owner
	owned="$$($(1) | grep -cx '$(XNAT_DIR_OWNER)' || true)"; \
	if [ "$$owned" != "$(words $(XNAT_DATA_DIRS))" ]; then \
		echo "❌  XNAT bind-mount sources under $(XNAT_DATA_DIR) are not all owned by $(XNAT_DIR_OWNER)."; \
		echo "    xnat-web runs as uid $(XNAT_CONTAINER_UID) and cannot write them, so DICOM"; \
		echo "    retrieval fails with the PACS reporting 'Peer aborted Association' and XNAT"; \
		echo "    writes no logs to explain it (FLIP#1095)."; \
		echo "    Fix: $(if $(2),$(2) ,)sudo chown -R $(XNAT_DIR_OWNER) $(XNAT_DATA_DIR)"; \
		exit 1; \
	fi
endef

# Path to the dev XNAT folder on the Docker host (bind mounts resolve there, not locally).
export XNAT_PATH=$(realpath .)

# XNAT_PLUGINS_FROM_HOST tracks STACK_FILES deliberately: only the development stack bind-mounts
# xnat/plugins over the image, so only development needs the host-side plugin cache (and the S3
# bucket and AWS credentials that populate it). Production bakes plugins into the image, and a
# trust host has neither FLIP_ARTIFACTS_BUCKET_NAME nor a reason to reach S3 for them.
ifeq ($(PROD),true)
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.production.yml
XNAT_PLUGINS_FROM_HOST =
else ifeq ($(PROD),stag)
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.production.yml
XNAT_PLUGINS_FROM_HOST =
else
# In development, mount plugins and config from the host for hot-reload
STACK_FILES = -c docker-compose-stack.yml -c docker-compose-stack.development.yml
XNAT_PLUGINS_FROM_HOST = 1
endif

# Guard: a KIT-selected target needs KIT set.
define require_kit
	@if [ -z "$(KIT)" ]; then \
	    echo "❌ KIT is required. Usage: make $@ KIT=<name>  (e.g. KIT=Trust_1)"; exit 1; \
	fi
endef

# Deploy-time guard: every XNAT password the compose stacks substitute is a
# bare ${VAR} with no default (docker-compose-stack.yml), so an empty value
# silently brings up a broken/insecure XNAT — admin auth 401s ("Site has not
# yet been configured" forever, see the export note above) and Postgres comes
# up with a blank superuser password — instead of failing. Refuse to deploy if
# any are empty. All of these come from the kit file: XNAT_ADMIN_*/XNAT_SERVICE_*
# are operator-set, XNAT_DATASOURCE_* are minted by `make register-trust` /
# `make generate-xnat-credentials`. Checks the exported process env (the same
# values `docker stack deploy` substitutes), not just Make vars.
# Beyond emptiness: refuse the committed kit-template placeholder for any of
# them, and the historical weak defaults for the minted XNAT_DATASOURCE_* pair
# (XNAT_ADMIN_*/XNAT_SERVICE_* are operator-set and legitimately simple in dev
# kits, so only the placeholder is rejected there). A skipped mint would
# otherwise reach the stock Postgres entrypoint as POSTGRES_PASSWORD — applied
# silently at first initdb, leaving the superuser password set to a literal
# committed across this public repo. xnat-web re-checks
# XNAT_DATASOURCE_PASSWORD at container start (wait-for-postgres.sh) and the
# xnat-db image guards both DB passwords itself (guard-xnat-db-passwords.sh,
# covering non-make deploys such as the K8s chart); this check just fails
# earliest, before a half-configured stack is deployed.
XNAT_REQUIRED_PASSWORDS := XNAT_ADMIN_INITIAL_PASSWORD XNAT_ADMIN_PASSWORD \
	XNAT_SERVICE_PASSWORD XNAT_DATASOURCE_PASSWORD XNAT_DATASOURCE_ADMIN_PASSWORD
XNAT_MINTED_PASSWORDS := XNAT_DATASOURCE_PASSWORD XNAT_DATASOURCE_ADMIN_PASSWORD
XNAT_CREDENTIAL_PLACEHOLDER := <run-make-generate-xnat-credentials>
define require_xnat_passwords
	@missing=""; weak=""; \
	for v in $(XNAT_REQUIRED_PASSWORDS); do \
		val="$$(printenv $$v 2>/dev/null)"; \
		if [ -z "$$val" ]; then missing="$$missing $$v"; \
		elif [ "$$val" = "$(XNAT_CREDENTIAL_PLACEHOLDER)" ]; then weak="$$weak $$v"; fi; \
	done; \
	for v in $(XNAT_MINTED_PASSWORDS); do \
		case "$$(printenv $$v 2>/dev/null)" in \
			xnat|password|admin|postgres) weak="$$weak $$v";; \
		esac; \
	done; \
	if [ -n "$$missing$$weak" ]; then \
		echo "❌ Refusing to deploy XNAT for KIT=$(KIT):"; \
		if [ -n "$$missing" ]; then echo "   empty password(s):$$missing"; fi; \
		if [ -n "$$weak" ]; then echo "   placeholder/weak password(s):$$weak"; fi; \
		echo "   These are substituted into the compose stack with no default, so an empty or"; \
		echo "   placeholder value yields a broken/insecure XNAT (Postgres applies its passwords"; \
		echo "   silently at first initdb). Set XNAT_ADMIN_*/XNAT_SERVICE_* in the kit file"; \
		echo "   ($(KIT_FILE)); mint XNAT_DATASOURCE_*/XNAT_ACTIVEMQ_PASSWORD into it"; \
		echo "   with 'make register-trust KIT=$(KIT)' or 'make generate-xnat-credentials'."; \
		exit 1; \
	fi
endef

# Build XNAT Docker images locally (mirrors CI workflow)
# Requires AWS CLI access to download WAR and plugins from S3
build:
	@echo "🔧 Building XNAT Docker images..."
	$(MAKE) xnat-war-download
	$(MAKE) xnat-plugins-download
	@echo "🐳 Building xnat-web image..."
	docker build \
		--build-arg XNAT_VERSION=${XNAT_VERSION} \
		--build-arg TOMCAT_XNAT_FOLDER=${TOMCAT_XNAT_FOLDER} \
		--build-arg XNAT_ROOT=${XNAT_ROOT} \
		--build-arg XNAT_HOME=${XNAT_HOME} \
		-t ${DOCKER_REGISTRY}xnat-web:${DOCKER_TAG} \
		xnat/
	@echo "🐳 Building xnat-db image..."
	docker build \
		--build-arg PG_VERSION=${PG_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-db:${DOCKER_TAG} \
		postgres/
	@echo "🐳 Building xnat-nginx image..."
	docker build \
		--build-arg NGINX_VERSION=${NGINX_VERSION} \
		-t ${DOCKER_REGISTRY}xnat-nginx:${DOCKER_TAG} \
		nginx/
	@echo "✅ XNAT Docker images built successfully!"

# Bring up every registered dev XNAT stack — the trust/.env.*.$(ENV) kits ARE
# the list (mirrors trust/Makefile's `up`). Each is a separate sub-make so its
# `-include ../.env.<CODE>.<env>` loads only that trust's kit file. `up-xnat`
# first blocks on `down-xnat` (via `xnat-stack-down`) before `xnat-reset` —
# otherwise a still-running xnat-web container holds the bind-mounted data
# dir's inode and the `sudo rm -rf` in `xnat-reset` silently leaves archive
# writes broken ("Source ... does not exist") so DICOM import fails on the
# next boot.
up:
	@found=0; for f in ../.env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#../.env.}; kit=$${kit%.$(ENV)}; found=1; \
	  $(MAKE) up-xnat KIT=$$kit || { echo "❌ Failed to start XNAT for trust $$kit"; exit 1; }; \
	done; \
	[ "$$found" = 1 ] || echo "ℹ️  No trust/.env.*.$(ENV) kits found — run 'make register-trusts' (from repo root) first."

# Bring up the XNAT swarm stack for one trust (KIT=Trust_1 / Trust_2).
# trust/Makefile's up-trust calls this so a trust comes up complete.
#
# In development, validate the version-matched, gitignored plugin cache before tearing down a
# working XNAT: a fresh clone downloads it here, a warm cache skips AWS, and a failure leaves the
# running stack untouched. Skipped outside development, where plugins come baked into the image
# (see XNAT_PLUGINS_FROM_HOST above).
up-xnat: create-xnat-network
	$(require_kit)
	$(require_xnat_passwords)
ifdef XNAT_PLUGINS_FROM_HOST
	$(MAKE) xnat-plugins-download
endif
	$(MAKE) down-xnat KIT=$(KIT)
	$(MAKE) xnat-reset KIT=$(KIT)
	XNAT_PATH=${XNAT_PATH} \
	XNAT_PORT=$(XNAT_PORT_EFFECTIVE) \
	PACS_UI_PORT=$(PACS_UI_PORT_EFFECTIVE) \
	DOCKER_NETWORK_NAME=$(XNAT_NETWORK) \
	docker stack deploy --with-registry-auth --detach=false ${STACK_FILES} $(XNAT_STACK)
	$(MAKE) xnat-configure XNAT_PROJECT=$(XNAT_STACK)

down:
	@for f in ../.env.*.$(ENV); do \
	  [ -e "$$f" ] || continue; \
	  kit=$${f#../.env.}; kit=$${kit%.$(ENV)}; \
	  $(MAKE) down-xnat KIT=$$kit; \
	done

down-xnat:
	$(require_kit)
	$(MAKE) xnat-stack-down STACK=$(XNAT_STACK)

# Get a shell in the running xnat-web container for the selected trust.
xnat-shell:
	$(require_kit)
	CONTAINER=$$(docker ps --filter "name=$(XNAT_STACK)_xnat-web" -q); \
	docker exec -it $$CONTAINER bash

# Remove a single XNAT Swarm stack and BLOCK until all its containers have
# stopped. `docker stack rm` is asynchronous; callers that then delete the
# stack's bind-mounted host directories (see `xnat-reset`) must wait for the
# containers to release those mounts first.
# Usage: $(MAKE) xnat-stack-down STACK=<stack-name>
xnat-stack-down:
	@[ -n "$(STACK)" ] || (echo "❌ STACK is required"; exit 1)
	@echo "🧹 Removing XNAT stack $(STACK) if present..."
	@docker stack rm $(STACK) 2>/dev/null || true
	@elapsed=0; \
	while [ -n "$$(docker ps -aq --filter label=com.docker.stack.namespace=$(STACK) 2>/dev/null)" ]; do \
		if [ $$elapsed -ge 120 ]; then \
			echo "❌ ERROR: $(STACK) containers still running after 120s"; exit 1; \
		fi; \
		echo "⏳ Waiting for $(STACK) containers to stop ($${elapsed}s)..."; \
		sleep 2; elapsed=$$((elapsed + 2)); \
	done
	@echo "✅ $(STACK) fully removed."

# Stop and clean up images
clean: down
	docker system prune -f

# Download XNAT WAR into the xnat-web Docker build context (skips if already present)
xnat-war-download:
	@mkdir -p xnat/build-artifacts
	@if [ -f xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war ]; then \
		echo "✅ XNAT WAR already exists locally."; \
	else \
		echo "📦 Downloading XNAT WAR from S3..."; \
		aws s3 cp "s3://$(FLIP_ARTIFACTS_BUCKET_NAME)/xnat-$(XNAT_VERSION)/xnat-web-$(XNAT_VERSION).war" \
			"xnat/build-artifacts/xnat-web-$(XNAT_VERSION).war"; \
	fi

# Ensure required XNAT plugins are present locally (skips S3 sync if all are available).
# The S3 artifact set is keyed by XNAT version — s3://<bucket>/xnat-<version>/{xnat-web-<version>.war,
# plugins/} — so different branches (e.g. main vs develop across an XNAT upgrade) can build against
# different WAR + plugin rosters without clobbering each other.
#
# A copied-but-unedited .env leaves the `<your-...>` placeholder in place, and a placeholder is not
# empty: an emptiness check passes it straight to the S3 sync, which dies on an opaque AWS
# bucket-name validation error instead of the actionable message below. Reject the placeholder shape
# too — the same idea as XNAT_CREDENTIAL_PLACEHOLDER above and unset_or_placeholder in
# deploy/providers/AWS/Makefile.
xnat-plugins-download:
	@case "$(FLIP_ARTIFACTS_BUCKET_NAME)" in \
	  "") reason="is unset" ;; \
	  "<"*">") reason="is still the .env placeholder ($(FLIP_ARTIFACTS_BUCKET_NAME))" ;; \
	  *) reason="" ;; \
	esac; \
	[ -z "$$reason" ] || { \
	  echo "❌ FLIP_ARTIFACTS_BUCKET_NAME $$reason — the host plugin cache cannot be filled."; \
	  echo "   Set it in .env.development (see the 'S3 Buckets' block), or pass it on the command"; \
	  echo "   line: make xnat-plugins-download FLIP_ARTIFACTS_BUCKET_NAME=<bucket>."; \
	  exit 1; }
	@./scripts/ensure_plugins.sh "xnat/plugins" "$(FLIP_ARTIFACTS_BUCKET_NAME)" "xnat-$(XNAT_VERSION)/plugins"

# Creates the data-volume directory structure for one trust's XNAT stack and
# wipes any previous data. Docker Swarm requires host directories to exist
# before bind-mounting; the wipe avoids stale bind mounts from a prior run
# (the second xnat-web otherwise crash-loops on a locked SQLite index).
# Data dir defaults vary by PROD — see the XNAT_DATA_DIR comment above
# for the resolution rule and how to override per-host.
#
# All three branches provision under sudo, development included. An unprivileged
# `mkdir -p` creates the tree owned by the invoking user, and the chown to 1001
# that follows then fails with EPERM on every host whose developer is not
# themselves uid 1001 — so the ownership the guard requires could never be
# reached, and `make up` would abort here. sudo costs nothing extra: the wipe on
# the line above already needs it, so dev bring-up already prompts, and sudo's
# credential cache means the mkdir and chown ride the same authentication.
#
# `set -e` also makes the two `docker context` probes fatal, which is deliberate.
# Letting them fall through to the local branch (the pre-FLIP#1095 behaviour) means
# a mistyped or unreachable DOCKER_CONTEXT silently runs `sudo rm -rf
# $(XNAT_DATA_DIR)` against the *operator's own machine* while they believe they
# are resetting a remote trust host. Aborting on an unresolvable context is the
# safer failure.
xnat-reset:
	$(require_kit)
	@case "$$(pwd)" in */trust/xnat) ;; *) \
		echo "ERROR: xnat-reset must be run from the trust/xnat/ directory (pwd: $$(pwd))"; exit 1;; \
	esac
	@if ! echo "$(XNAT_PORT_EFFECTIVE)" | grep -qE '^[0-9]+$$'; then \
		echo "ERROR: XNAT_PORT must be numeric for KIT=$(KIT), got '$(XNAT_PORT_EFFECTIVE)'"; exit 1; \
	fi
	@set -e; \
	echo "Deleting previous XNAT data and creating new directories for $(KIT) ..."; \
	if [ "$(PROD)" = "true" ] || [ "$(PROD)" = "stag" ]; then \
		echo "  📍 Using prod data dir: $(XNAT_DATA_DIR)/"; \
		xnat_ctx="$$(docker context show)"; \
		xnat_endpoint="$$(docker context inspect "$$xnat_ctx" --format '{{.Endpoints.docker.Host}}' 2>/dev/null)"; \
		case "$$xnat_endpoint" in \
			ssh://*) \
				echo "  🛰️  remote swarm deploy (context '$$xnat_ctx') — resetting XNAT dirs on the trust host via ssh"; \
				ssh "$$xnat_ctx" "sudo rm -rf $(XNAT_DATA_DIR) && sudo mkdir -p $(XNAT_DATA_DIRS) && sudo chown -R $(XNAT_DIR_OWNER) $(XNAT_DATA_DIR)"; \
				$(call check_xnat_dir_owner,ssh "$$xnat_ctx" "stat -c '%u:%g' $(XNAT_DATA_DIRS)",ssh $$xnat_ctx); \
				;; \
			*) \
				echo "  💻 local docker context '$$xnat_ctx' ($$xnat_endpoint) — resetting XNAT dirs on this host"; \
				sudo rm -rf $(XNAT_DATA_DIR); \
				sudo mkdir -p $(XNAT_DATA_DIRS); \
				sudo chown -R $(XNAT_DIR_OWNER) $(XNAT_DATA_DIR); \
				$(call check_xnat_dir_owner,stat -c '%u:%g' $(XNAT_DATA_DIRS)); \
				;; \
		esac; \
	else \
		echo "  📍 Using dev data dir: $(XNAT_DATA_DIR)/"; \
		sudo rm -rf $(XNAT_DATA_DIR); \
		sudo mkdir -p $(XNAT_DATA_DIRS); \
		sudo chown -R $(XNAT_DIR_OWNER) $(XNAT_DATA_DIR); \
		$(call check_xnat_dir_owner,stat -c '%u:%g' $(XNAT_DATA_DIRS)); \
	fi

# Configure an XNAT instance with initial settings and run dcm2niix setup.
# Usage: make xnat-configure XNAT_PROJECT=<stack-name>
# The `rm -f` before each tee: /data/xnat/config is a host bind mount, and log
# files left by pre-hardening deploys (root-running xnat-web) are root-owned —
# the non-root xnat user can't open them for truncation (tee still streams but
# exits non-zero under pipefail, aborting the configure chain). It can unlink
# them, though: the config dir itself is xnat-writable.
xnat-configure:
	@[ -n "$(XNAT_PROJECT)" ] || (echo "❌ XNAT_PROJECT is required"; exit 1)
	@echo "⏳ Waiting for $(XNAT_PROJECT)_xnat-web container to be ready..."
	@elapsed=0; while [ "$$(docker ps --filter "name=$(XNAT_PROJECT)_xnat-web" -q)" = "" ]; do \
		if [ $$elapsed -ge 120 ]; then echo "❌ ERROR: $(XNAT_PROJECT)_xnat-web container never started"; exit 1; fi; \
		echo "  Waiting for container..."; sleep 5; elapsed=$$((elapsed + 5)); \
	done
	CONTAINER=$$(docker ps --filter "name=$(XNAT_PROJECT)_xnat-web" -q); \
	[ -n "$$CONTAINER" ] || { echo "❌ ERROR: $(XNAT_PROJECT)_xnat-web container not found at configure time"; exit 1; }; \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-xnat-$(XNAT_PROJECT).log" && rm -f "$$LOG_FILE" && bash configure-xnat.sh 2>&1 | tee "$$LOG_FILE"' && \
	docker exec $$CONTAINER bash -c 'set -o pipefail; cd /data/xnat/config && LOG_FILE="configure-dcm2niix-$(XNAT_PROJECT).log" && rm -f "$$LOG_FILE" && bash configure-dcm2niix.sh 2>&1 | tee "$$LOG_FILE"'

# Guard: XNAT and the trust core services must share one network. up-trust brings the trust
# compose up before invoking us, so the trust's real attachment is observable — compare it
# rather than assume both sides derived the same FLIP_INSTANCE. A mismatch (most likely the
# standalone path with FLIP_INSTANCE unset) otherwise deploys a healthy-looking XNAT that
# imaging-api cannot reach, surfacing much later as an opaque image-pull failure.
define check_network_matches_trust
	@actual=$$(docker ps --filter "label=com.docker.compose.project=$(TRUST_PROJECT)" -q 2>/dev/null | head -1); \
	if [ -n "$$actual" ]; then \
	  nets=$$(docker inspect "$$actual" --format '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}} {{end}}' 2>/dev/null); \
	  case " $$nets " in \
	    *" $(XNAT_NETWORK) "*) ;; \
	    *) echo "❌ XNAT would use $(XNAT_NETWORK), but $(TRUST_PROJECT) is on: $$nets"; \
	       echo "   They must share a trust network or imaging-api cannot reach XNAT."; \
	       echo "   Re-run with FLIP_INSTANCE set to the instance that owns $(TRUST_PROJECT)"; \
	       echo "   (or unset it, if that trust belongs to the default instance)."; \
	       exit 1;; \
	  esac; \
	fi
endef

create-xnat-network:
	$(require_kit)
	$(check_network_matches_trust)
	@echo "🔧 Ensuring external Docker network $(XNAT_NETWORK) exists..."
	@docker network inspect $(XNAT_NETWORK) >/dev/null 2>&1 || \
		docker network create --driver overlay --attachable $(XNAT_NETWORK)

# Anonymization-script tests (pure Python; no XNAT or Docker required)
test unit_test local_test:
	$(MAKE) -C tests $@
