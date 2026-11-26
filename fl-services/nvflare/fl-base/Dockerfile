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

FROM python:3.13-slim-bookworm AS base

ARG LOCAL_DEV=false
# PYTHONSAFEPATH stops CPython prepending the "unsafe" path — the script's directory, or the
# current working directory under `python -m`/`python -c`. Both the server and the client are
# started as `python3 -u -m nvflare.private.fed.app.*` from inside the FLARE workspace (/app
# unless FL_WORKSPACE moves it), which is world-writable (the `chmod 1777` below), so without
# this the workspace is the FIRST entry on sys.path of the long-lived FL process: a file a job
# leaves in it shadows any module imported later, including by the next job. NVFLARE then hands
# the parent's sys.path to every job worker it spawns verbatim
# (nvflare.utils.job_launcher_utils.add_custom_dir_to_path builds the child's PYTHONPATH from
# os.pathsep.join(sys.path)), so keeping the entry out of the parent is what keeps it out of the
# workers. PYTHONPATH entries are NOT filtered by PYTHONSAFEPATH, which is why the placeholder
# below matters: FLARE's own startup/sub_start.sh does `export PYTHONPATH=/local/custom:$PYTHONPATH`,
# and with PYTHONPATH unset that trailing colon is an EMPTY entry — i.e. the working directory,
# straight back onto sys.path. A non-empty, root-owned, empty directory makes that expansion
# harmless. Keep both: neither alone closes the path.
# PYTHONDONTWRITEBYTECODE additionally stops the runtime writing .pyc files next to the modules
# it imports (the venv ships its bytecode, --compile-bytecode below).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1 \
    PYTHONPATH=/opt/flip/empty-pythonpath \
    PATH="/app/.venv/bin:${PATH}"
RUN mkdir -p /opt/flip/empty-pythonpath && chmod 555 /opt/flip/empty-pythonpath

# Minimal OS deps including procps for pkill
RUN apt-get update && \
    apt-get install -y --no-install-recommends procps ca-certificates gettext-base && \
    rm -rf /var/lib/apt/lists/*

# Add uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Create a non-root user so the FL server and client containers never run as root
# (GHSA-8465). The identity is FIXED at flip/1000/1000 and is deliberately no
# longer wired from the building developer's environment (FLIP#1171): a published
# image is always `flip`, so the compose files can name /home/flip literally and a
# locally-built :dev image is identical in identity to CI's :stag. Baking the host
# developer's identity instead made the two diverge, and the dev composes — which
# reuse it as runtime config (HOME, the ~/.aws mount path) — then failed three
# separate ways on any host whose developer is not uid 1000: the bind-mounted kit
# dirs, $HOME, and the AWS SSO cache. Runtime identity is now decoupled: the dev
# composes pass `user: "${UID}:1000"`, so the process runs as the *host* user (which
# owns the bind mounts and the 0600 SSO token) while keeping this image's gid.
# The args are kept, rather than hardcoded below, so the values still appear once
# at the top and an out-of-tree build can still override them; nothing in-tree does.
# 1000 also matches the ubuntu uid that EC2/on-prem trust hosts stage kits as, which
# is why it was chosen — see deploy/aws/efs.tf.
# USER is NOT set here — derived images (fl-server, fl-client) switch after their
# own COPY/RUN so the user owns the files those instructions create. Created
# before the uv sync below so the final chown lands in the same layer as the
# venv it's chowning, rather than copying the whole (multi-GB) venv up into a
# separate layer — see the sync+chown RUN for why that ordering matters.
ARG UID=1000
ARG GID=1000
ARG UNAME=flip
RUN groupadd -g ${GID} ${UNAME} && \
    useradd -u ${UID} -g ${GID} -m -d /home/${UNAME} -s /bin/bash ${UNAME}

# Sync the project into a new environment, using the frozen lockfile.
# Copy necessary files for building the flip package. They land in /opt/flip-utils, NOT in the
# world-writable workspace at /app and NOT owned by the runtime user — the same split the Flower
# fl-base already makes (fl-services/flower/fl-base/Dockerfile). flip-utils is installed from
# here as an ordinary (non-editable) package below, so this tree is build input only: nothing
# imports it at run time, and no process can rewrite the flip package every later job imports.
COPY flip-utils/pyproject.toml flip-utils/uv.lock flip-utils/README.md /opt/flip-utils/
COPY flip-utils/docs/ /opt/flip-utils/docs/
COPY flip-utils/flip/ /opt/flip-utils/flip/

# Sync dependencies using uv into the venv at /app/.venv (UV_PROJECT_ENVIRONMENT; the path is
# part of this image's contract — the chart's job-pod pruner calls /app/.venv/bin/python).
# If LOCAL_DEV is set to true, install dev dependencies as well, else --no-dev
# --frozen ensures exact versions from uv.lock are used
# Install the nvflare extra (this image's FL backend; FLIP#1047) and the full ML extra
# --compile-bytecode ships the venv's .pyc files (FLIP#1782). PYTHONDONTWRITEBYTECODE
# stops runtime writes only, so without them every FL job process compiled torch,
# MONAI and NVFLARE from source on each start (~7-11 s, 2.6 s with them). It also means the
# runtime never needs to write anywhere under the venv, so the venv can stay read-only to it.
# --no-editable installs flip-utils as an ordinary package: the built wheel goes into
# site-packages, and /opt/flip-utils (the source) does NOT go onto sys.path, which an editable
# install would have arranged via a .pth file. `import flip` therefore resolves to the venv,
# nothing else, and there is one copy of it to protect.
#
# NOTHING here is chowned to the runtime user. Everything the FL processes import — the venv and
# its site-packages — is root-owned and read-only to them, so job code cannot rewrite a module
# that the client/server process, or the next job, imports. The runtime user never writes inside
# the venv (bytecode is precompiled above), so it needs no ownership of it.
#
# The chmods are deliberately NOT recursive (FLIP#1171): the venv is ~7GB and overlayfs copies up
# every byte a later layer touches, so chaining them onto this RUN rather than adding a layer is
# what keeps the image from roughly doubling. The dev composes run this image as the host user's
# uid with this image's gid, and the only writes that then need permission are *new entries* in
# two directories: FLARE creates /app/pid.fl and a /app/<job-id>/ workspace per run, and torch
# creates $HOME/.cache when the model persistor builds its model. Creating an entry needs write
# on the directory, not on its contents.
# /app itself is world-writable, sticky: a kit's FLIP_UID (FLIP#1731) is neither this user nor in
# its group and still has to create those entries. Sticky is what makes that safe now that /app
# holds the venv: a non-owner cannot unlink or rename root-owned .venv, only add entries beside
# it — and those entries are not importable, because /app is no longer on sys.path (see
# PYTHONSAFEPATH above).
RUN cd /opt/flip-utils && \
    UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-cache --compile-bytecode --no-editable \
      --extra nvflare --extra full $(if [ "$LOCAL_DEV" = "true" ]; then echo "--dev"; else echo "--no-dev"; fi) && \
    chmod 1777 /app && chmod g+rwX /home/${UNAME}
