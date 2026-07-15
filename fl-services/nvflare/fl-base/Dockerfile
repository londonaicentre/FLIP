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

FROM python:3.12-slim-bookworm AS base

ARG LOCAL_DEV=false
# Set log level
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Minimal OS deps including procps for pkill
RUN apt-get update && \
    apt-get install -y --no-install-recommends procps ca-certificates gettext-base && \
    rm -rf /var/lib/apt/lists/*

# Add uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Sync the project into a new environment, using the frozen lockfile
# Copy necessary files for building the flip package
COPY flip-utils/pyproject.toml flip-utils/uv.lock flip-utils/README.md ./
COPY flip-utils/docs/ ./docs/
COPY flip-utils/flip/ ./flip/

# Sync dependencies using uv
# If LOCAL_DEV is set to true, install dev dependencies as well, else --no-dev
# --frozen ensures exact versions from uv.lock are used
# Install with full optional dependencies for ML frameworks
RUN uv sync --frozen --no-cache --extra full $(if [ "$LOCAL_DEV" = "true" ]; then echo "--dev"; else echo "--no-dev"; fi)

# Create a non-root user so the FL server and client containers never run as root
# (GHSA-8465). UID/GID/UNAME are build args wired by the compose files so the
# container user matches the host UID that owns bind-mounted provision directories
# in dev. In prod (no bind-mounts), the defaults (UID 1000 / GID 1000 / flip) apply.
# USER is NOT set here — derived images (fl-server, fl-client) switch after their
# own COPY/RUN so the user owns the files those instructions create.
ARG UID
ARG GID
ARG UNAME
RUN groupadd -g ${GID} ${UNAME} && \
    useradd -u ${UID} -g ${GID} -m -d /home/${UNAME} -s /bin/bash ${UNAME}

RUN chown -R ${UNAME}:${UNAME} /app
