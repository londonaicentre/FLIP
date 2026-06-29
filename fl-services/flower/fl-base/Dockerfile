# syntax=docker/dockerfile:1.4

# Copyright (c) 2026 Flower Labs GmbH
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

FROM flwr/base:${FLWR_VERSION:-1.32.0-py3.12-ubuntu24.04} AS base

ARG LOCAL_DEV=false
WORKDIR /app

# Install flip-utils from the IN-REPO source (build context = repo root). This
# replaces two older mechanisms now that flip-utils lives in this monorepo:
#   * the install of deps from one hard-coded tutorial pyproject.toml (footgun #37), and
#   * the `fliputils_dist` external-wheel `development` target (now deprecated).
# It is installed into flwr/base's own Python so the `flwr` CLI and the `flip`
# package coexist. The `full` extra brings the ML deps Flower apps need
# (torch/MONAI/etc.); flip-utils' readme points at docs/overview.rst and its
# version at flip/__init__.py, so both are copied.
#
# --extra-index-url points pip at PyTorch's CUDA-12.8 wheel index. Combined with
# flip-utils' "torch>=2.11,<2.12" pin, pip picks torch 2.11.x+cu128 — the highest
# torch that runs on the host NVIDIA driver 575.x (cu13 wheels need driver >=580).
# --chown=app:app: flwr/base runs as the non-root `app` user, and COPY defaults
# to root ownership — so without this the `rm -rf /tmp/flip-utils` below fails
# with "Permission denied" (app cannot unlink root-owned files under sticky /tmp).
COPY --chown=app:app flip-utils/pyproject.toml flip-utils/README.md /tmp/flip-utils/
COPY --chown=app:app flip-utils/docs/ /tmp/flip-utils/docs/
COPY --chown=app:app flip-utils/flip/ /tmp/flip-utils/flip/
RUN python -m pip install -U --no-cache-dir "/tmp/flip-utils[full]" \
      --extra-index-url https://download.pytorch.org/whl/cu128 \
  && rm -rf /tmp/flip-utils

# Copy register-supernode-keys.sh (used by the secure / central-hub topology to
# register SuperNode public keys with the SuperLink).
COPY --chown=app:app fl-services/flower/register-supernode-keys.sh /scripts/register-supernode-keys.sh
