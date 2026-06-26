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
# Flower FL service targets — build, provision (TLS certs), and standalone run
# (insecure `up` / secure `up-secure` + `submit`), mirroring flip-fl-base-flower's
# Makefile. The root Makefile forwards build-fl here by FL_BACKEND; the run/provision/
# submit targets are invoked here directly (make -C fl-services/flower <target>), which
# is where Flower FL deployments are tested. The central-hub multi-net topology is the
# separate deploy/compose.development.flower.yml (driven by the root `make up`).

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

ifneq ("$(wildcard $(REPO_ROOT)/.env.development)","")
include $(REPO_ROOT)/.env.development
export $(shell sed 's/=.*//' $(REPO_ROOT)/.env.development)
endif
include $(REPO_ROOT)/deploy/fl_backend.mk

# Run container processes under the host's group so bind-mounted files (owned by the
# host user) stay writable. compose reads ${DOCKER_GID} in each service's group_add.
export DOCKER_GID := $(shell id -g)

DEV_COMPOSE      := docker compose -f $(REPO_ROOT)/fl-services/flower/compose.dev.yml
SECURE_COMPOSE   := docker compose -f $(REPO_ROOT)/fl-services/flower/compose.secure.yml
PROVISION_SCRIPT := $(REPO_ROOT)/fl-services/flower/provision/scripts/generate-tls-certificates.sh
LOCAL_DEV        ?= false
NET_NUMBER       ?= 1
APP              ?= numpy
# Host dir bind-mounted as /app/runs (Flower run state); gitignored.
export WORKING_DIR := $(REPO_ROOT)/fl-services/flower/runs

.PHONY: build provision generate-tls-certificates-net-1 generate-tls-certificates-net-2 \
        up up-secure down submit _ensure-dev-dirs

# Build the flower-* :dev images. flwr-base builds first (own invocation) — a plain
# FROM flower-fl-base in the derived Dockerfiles is invisible to Compose's build graph.
build:
	@echo "🛠️  Building flower-fl-base:dev (LOCAL_DEV=$(LOCAL_DEV))..."
	LOCAL_DEV=$(LOCAL_DEV) $(DEV_COMPOSE) --profile build-only build flwr-base
	@echo "🛠️  Building flower-{fl-api,superlink,supernode}:dev..."
	$(DEV_COMPOSE) build fl-api superlink supernode-1

# Provision per-net TLS certs + SuperNode keys → fl-services/flower/provision/creds/net-<N>.
provision:
	@bash $(PROVISION_SCRIPT) net-$(NET_NUMBER)
generate-tls-certificates-net-1:
	@bash $(PROVISION_SCRIPT) net-1
generate-tls-certificates-net-2:
	@bash $(PROVISION_SCRIPT) net-2

# Ensure the runs dir + placeholder dev-data mounts exist so `up` doesn't fail on the
# bind mounts. Override DEV_IMAGES_DIR / DEV_DATAFRAME to run against real tutorial data.
_ensure-dev-dirs:
	@mkdir -p $(WORKING_DIR) /tmp/flwr-dev-images && chmod 777 $(WORKING_DIR) && touch /tmp/flwr-dev-dataframe.csv

# Standalone INSECURE FL stack (1 SuperLink + 2 SuperNodes + fl-api), no hub/trusts.
up: _ensure-dev-dirs
	$(DEV_COMPOSE) -p flwr up --remove-orphans

# Standalone SECURE (TLS + SuperNode auth) stack. Needs `make provision` first.
up-secure: _ensure-dev-dirs
	@if [ ! -f "$(REPO_ROOT)/fl-services/flower/provision/creds/net-$(NET_NUMBER)/certificates/ca.crt" ]; then \
		echo "❌ Flower TLS certs (net-$(NET_NUMBER)) not found. Run 'make provision NET_NUMBER=$(NET_NUMBER)' first."; \
		exit 1; \
	fi
	$(SECURE_COMPOSE) -p flwr up --remove-orphans

down:
	$(DEV_COMPOSE) -p flwr down -v --remove-orphans

# Submit a job to the running stack (no published host port): exec into fl-api and POST
# to its loopback API (the image ships Python, not curl). Usage: make submit APP=<folder>
submit:
	$(DEV_COMPOSE) -p flwr exec -T fl-api \
		python -c "import urllib.request as u; print(u.urlopen(u.Request('http://localhost:8000/submit_run/$(APP)', method='POST')).read().decode())"
