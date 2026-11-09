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

FROM flwr/base:${FLWR_VERSION:-1.42.0-py3.12-ubuntu24.04} AS base

ARG LOCAL_DEV=false
# torch's native Triton kernels compile a C helper on first use, and this image has no C compiler:
# without this, a GPU backward through one of them fails (FLIP#1700). torch reads it at import.
ENV TORCH_DISABLE_NATIVE_JIT=1
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
# The ServerApp and ClientApps run in this environment: nothing installs an
# app's dependencies at run time (FLIP#1418), and the install below is not
# editable, so runs import flip-utils from site-packages, never from
# /opt/flip-utils. The source is kept there for anything that does `uv sync`
# an fl-apps template inside this image (Flower's per-run install, were it
# re-enabled): the templates' `[tool.uv.sources] flip-utils = { path =
# "/opt/flip-utils" }` resolves against this copy rather than a PyPI release
# (#767), and fails without it.
#
# --extra-index-url points pip at PyTorch's CUDA-13.0 wheel index, where the lock's
# torch 2.13.x+cu130 lives.
# COPY's default root ownership is deliberate: user-submitted apps execute in
# this container as the same non-root `app` user that flwr/base runs as, so an
# app-writable copy could be mutated by app code, poisoning anything that later
# resolves the templates' pin against it. Root-owned, the tree is read-only to
# `app` — enough for the pip install below and any `uv sync` of a template,
# which only read it (flip-utils' hatchling backend builds in temp dirs, never
# the source tree).
COPY flip-utils/pyproject.toml flip-utils/uv.lock flip-utils/README.md /opt/flip-utils/
COPY flip-utils/docs/ /opt/flip-utils/docs/
COPY flip-utils/flip/ /opt/flip-utils/flip/
# Dependencies come from flip-utils' uv.lock, hash-checked, as in the NVFLARE fl-base (#1801), so its
# security floors reach this image through the lock. flwr is not in the export (flip-utils' dev group):
# the image keeps flwr/base's own, and `pip check` fails the build if the locked packages no longer fit it.
RUN cd /opt/flip-utils \
 && python -m uv export --frozen --extra full --no-dev --no-emit-project -o /tmp/flip-utils.txt \
 && python -m pip install --no-cache-dir --require-hashes -r /tmp/flip-utils.txt \
      --extra-index-url https://download.pytorch.org/whl/cu130 \
 && python -m pip install --no-cache-dir --no-deps /opt/flip-utils \
 && python -m pip check \
 && rm /tmp/flip-utils.txt

# Copy register-supernode-keys.sh (used by the secure / central-hub topology to
# register SuperNode public keys with the SuperLink).
COPY --chown=app:app fl-services/flower/register-supernode-keys.sh /scripts/register-supernode-keys.sh

# HOME is /app (the flwr/base image's own). A kit's FLIP_UID (FLIP#1731) owns nothing here, so
# the SuperNode's ~/.flwr and the caches under ~/.cache need /app writable by any uid, sticky.
RUN mkdir -p /app/.cache && chmod 1777 /app /app/.cache
