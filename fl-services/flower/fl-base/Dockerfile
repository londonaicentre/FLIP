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

FROM flwr/base:${FLWR_VERSION:-1.38.0-py3.12-ubuntu24.04} AS base

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
# The ServerApp and ClientApps run in this environment: nothing installs an
# app's dependencies at run time (FLIP#1418), and the install below is not
# editable, so runs import flip-utils from site-packages, never from
# /opt/flip-utils. The source is kept there for anything that does `uv sync`
# an fl-apps template inside this image (Flower's per-run install, were it
# re-enabled): the templates' `[tool.uv.sources] flip-utils = { path =
# "/opt/flip-utils" }` resolves against this copy rather than a PyPI release
# (#767), and fails without it.
#
# --extra-index-url points pip at PyTorch's CUDA-13.0 wheel index. Combined with
# flip-utils' "torch>=2.13,<2.14" pin, pip picks torch 2.13.x+cu130.
# COPY's default root ownership is deliberate: user-submitted apps execute in
# this container as the same non-root `app` user that flwr/base runs as, so an
# app-writable copy could be mutated by app code, poisoning anything that later
# resolves the templates' pin against it. Root-owned, the tree is read-only to
# `app` — enough for the pip install below and any `uv sync` of a template,
# which only read it (flip-utils' hatchling backend builds in temp dirs, never
# the source tree).
COPY flip-utils/pyproject.toml flip-utils/README.md /opt/flip-utils/
COPY flip-utils/docs/ /opt/flip-utils/docs/
COPY flip-utils/flip/ /opt/flip-utils/flip/
# flwr/base ships flwr==1.38.0, which pins cryptography>=46.0.7,<47.0.0 (GHSA-537c-gmf6-5ccf
# needs >=48.0.1). This is a plain `pip install`, not a `uv sync`, so the
# `[tool.uv] override-dependencies` escape hatch used in fl-api-flower/pyproject.toml
# doesn't apply here — listing cryptography directly on the install line is the pip
# equivalent, forcing the upgrade past flwr's declared ceiling. Safe for the same reason
# recorded there: flwr only uses cryptography for TLS cert loading in secure aggregation,
# a stable surface across majors.
# pip does not apply flip-utils' UV constraints; carry its security floors here too: setuptools, and certifi
# and uv (GHSA-6q48-6c7w-9gw2, GHSA-hq85-5cpj-mr62), which flwr/base ships and flwr pins below the uv fix.
RUN python -m pip install -U --no-cache-dir "/opt/flip-utils[full]" "cryptography>=50.0.0" "setuptools>=83.0.0" \
      "certifi>=2026.10.19" "uv>=0.13.4" \
      --extra-index-url https://download.pytorch.org/whl/cu130

# Copy register-supernode-keys.sh (used by the secure / central-hub topology to
# register SuperNode public keys with the SuperLink).
COPY --chown=app:app fl-services/flower/register-supernode-keys.sh /scripts/register-supernode-keys.sh
