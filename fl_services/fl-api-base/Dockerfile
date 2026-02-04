# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

FROM python:3.12-slim-bookworm AS development

RUN apt-get update && apt-get install -y gettext-base \
    && rm -rf /var/lib/apt/lists/*

ARG UID
ARG GID
ARG UNAME
RUN groupadd -g ${GID} ${UNAME}
# Create a user with the same UID and GID as the host user with home directory at /home/${UNAME}
RUN useradd -u ${UID} -g ${GID} -m -d /home/${UNAME} -s /bin/bash ${UNAME}
WORKDIR /app
RUN chown -R ${UNAME}:${UNAME} /app

# Add uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Sync the project into a new environment, using the frozen lockfile
COPY pyproject.toml uv.lock ./

COPY ./entrypoint.sh /app/entrypoint.sh
# COPY ./admin /app/admin
RUN chown -R ${UNAME}:${UNAME} /app

RUN chmod +x /app/entrypoint.sh
WORKDIR /app
USER ${UNAME}

# Sync dependencies using uv
RUN uv sync

# Add fastapi executable to path
ENV PATH="/app/.venv/bin:$PATH"

# Expose port (use 8000 since we dropped NGINX and aren't using a socket)
EXPOSE 8000
EXPOSE 5679

# Run FastAPI application using uvicorn via uv
CMD ["bash", "-c", "/app/entrypoint.sh"]

FROM development AS production
COPY --chown=${UNAME}:${UNAME} fl_api/ /app/fl_api/

# NOTE the transfer dir in the new provisioning is not straightforward to configure - for now, we keep "transfer"
# as both the download_dir and upload_dir in fed_admin.json
RUN mkdir -p /app/admin/transfer && chown -R ${UNAME}:${UNAME} /app/admin

CMD ["bash", "-c", "/app/entrypoint.sh"]