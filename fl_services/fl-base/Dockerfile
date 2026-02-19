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
COPY pyproject.toml uv.lock README.md ./
COPY flip/ ./flip/

# Sync dependencies using uv
# If LOCAL_DEV is set to true, install dev dependencies as well, else --no-dev
# --frozen ensures exact versions from uv.lock are used
# Install with full optional dependencies for ML frameworks
RUN uv sync --frozen --no-cache --extra full $(if [ "$LOCAL_DEV" = "true" ]; then echo "--dev"; else echo "--no-dev"; fi)