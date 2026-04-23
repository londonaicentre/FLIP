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

# Single source of truth for FL backend selection, derived Docker image names,
# and the provisioned-workspace path used by the dev compose overlays.
# Included from the root Makefile and trust/Makefile after the .env* include block.
# Any .env* file value for DOCKER_FL_API_NAME / DOCKER_FL_SERVER_NAME /
# DOCKER_FL_CLIENT_NAME / FL_PROVISIONED_DIR is overridden here on purpose —
# these must match FL_BACKEND. To test a one-off image or a custom workspace,
# override on the command line (CLI args outrank Makefile assignments):
#   make up DOCKER_FL_SERVER_NAME=ghcr.io/foo/custom:test
#   make up FL_PROVISIONED_DIR=/tmp/my-workspace
#
# FL_PROVISIONED_DIR is only read by deploy/compose.development.{flower,nvflare}.yml
# (volume mounts of the provisioned certs / workspace from the sibling repo).
# In stag/prod the FL services pull their kit from S3 and this path is unused —
# the root Makefile still absolutises it for consistency but no prod compose
# references ${FL_PROVISIONED_DIR}.

FL_BACKEND ?= flower
VALID_FL_BACKENDS := flower nvflare

ifeq (,$(filter $(FL_BACKEND),$(VALID_FL_BACKENDS)))
$(error Invalid FL_BACKEND '$(FL_BACKEND)'. Must be one of: $(VALID_FL_BACKENDS))
endif

ifeq ($(FL_BACKEND),nvflare)
DOCKER_FL_API_NAME    := flare-fl-api
DOCKER_FL_SERVER_NAME := flare-fl-server
DOCKER_FL_CLIENT_NAME := flare-fl-client
FL_PROVISIONED_DIR    := ../flip-fl-base/workspace
else
DOCKER_FL_API_NAME    := flower-fl-api
DOCKER_FL_SERVER_NAME := flower-superlink
DOCKER_FL_CLIENT_NAME := flower-supernode
FL_PROVISIONED_DIR    := ../flip-fl-base-flower/certs
endif

export DOCKER_FL_API_NAME DOCKER_FL_SERVER_NAME DOCKER_FL_CLIENT_NAME FL_PROVISIONED_DIR
