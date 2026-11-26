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

FROM flwr/base:${FLWR_VERSION:-1.43.0-py3.13-ubuntu24.04} AS base

ARG LOCAL_DEV=false
WORKDIR /app
# flwr/base on its own: a conflict it ships fails this step, not the `pip check` after flip-utils' install.
RUN python -m pip check

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
# --extra-index-url points pip at PyTorch's CUDA-13.2 wheel index, where the lock's
# torch 2.15.x+cu132 lives.
# COPY's default root ownership is deliberate: user-submitted apps execute in
# this container as the same non-root `app` user that flwr/base runs as, so an
# app-writable copy could be mutated by app code, poisoning anything that later
# resolves the templates' pin against it. Root-owned, the tree is read-only to
# `app` — enough for the wheel build below and any `uv sync` of a template,
# which only read it (flip-utils' hatchling backend builds in temp dirs, never
# the source tree).
COPY flip-utils/pyproject.toml flip-utils/uv.lock flip-utils/README.md /opt/flip-utils/
COPY flip-utils/docs/ /opt/flip-utils/docs/
COPY flip-utils/flip/ /opt/flip-utils/flip/
# Dependencies come from flip-utils' uv.lock, hash-checked, as in the NVFLARE fl-base (#1801), so its
# security floors reach this image through the lock. The `flower` extra, not `nvflare` (FLIP#1047): its flwr is
# flwr/base's own version (test_flip_utils_lock_carries_the_fl_base_images_flwr pins the two), so pip keeps that
# install and the lock pins flwr's dependencies; `pip check` fails the build if they no longer fit.
# uv builds the flip-utils wheel, so hatchling comes within the 72h cooldown; pip would fetch the newest.
#
# The install runs as root and ends by handing the whole import path to root, read-only to everyone
# else (FLIP#1573). flwr/base ships `/python/venv` owned by `app` and ends `USER app`, so an install
# left as-is gives the ServerApp and the ClientApps write access to the environment they import:
# app code could rewrite `flip`, `flwr` or `torch` in site-packages and every later run in that
# container would import the rewritten copy. Since FLIP#1418 the image environment is the only
# environment a run gets, so it has to survive the run.
#
# It is all one RUN on purpose. A `chown -R` in a later layer rewrites every file it touches into
# that layer, which for this venv (torch + MONAI) added ~7 GB to an image every trust pulls; in the
# layer that installs them there is nothing to duplicate.
#
# Both prefixes: `sys.prefix` is the venv, `sys.base_prefix` the interpreter whose stdlib it reuses —
# together they cover every sys.path entry outside the app's own working dir.
#
# compileall runs while the tree is still writable, so no import needs to write bytecode afterwards
# (an uncached import still works read-only, it just recompiles on every start). It tolerates
# failures because site-packages legitimately carries .py files that do not compile under this
# interpreter — vendored samples, py2 and non-UTF-8 fixtures; a real breakage surfaces in the
# `pip check` above it and in fl-services/flower/scripts/check_image_env_readonly.py, which asserts
# the flip and flwr trees are fully cached.
#
# `chmod -R go-w` only clears write bits, so exec bits on the venv's console scripts survive.
#
# HOME is /app for root too, so anything uv or pip caches lands root-owned in the app's own HOME:
# `--no-cache` on both and the `rm -rf /app/.cache` below keep it out of the image, and keep the
# final `chmod 1777 /app/.cache` (which runs as `app`) able to do its job.
USER root
RUN cd /opt/flip-utils \
 && python -m uv export --frozen --no-cache --extra flower --extra full --no-dev --no-emit-project -o /tmp/flip-utils.txt \
 && python -m pip install --no-cache-dir --require-hashes -r /tmp/flip-utils.txt \
      --extra-index-url https://download.pytorch.org/whl/cu132 \
 && python -m uv build --wheel --no-cache --out-dir /tmp/dist \
 && python -m pip install --no-cache-dir --no-deps /tmp/dist/*.whl \
 && python -m pip check \
 && rm -rf /tmp/flip-utils.txt /tmp/dist /app/.cache \
 && PREFIXES="$(python -c 'import sys; print(sys.prefix, sys.base_prefix)')" \
 && { python -m compileall -q $PREFIXES || echo "compileall: some files did not compile (tolerated, see above)"; } \
 && chown -R root:root $PREFIXES \
 && chmod -R go-w $PREFIXES
USER app

# Copy register-supernode-keys.sh (used by the secure / central-hub topology to
# register SuperNode public keys with the SuperLink).
COPY --chown=app:app fl-services/flower/register-supernode-keys.sh /scripts/register-supernode-keys.sh

# HOME is /app (the flwr/base image's own). A kit's FLIP_UID (FLIP#1731) owns nothing here, so
# the SuperNode's ~/.flwr and the caches under ~/.cache need /app writable by any uid, sticky.
RUN mkdir -p /app/.cache && chmod 1777 /app /app/.cache
