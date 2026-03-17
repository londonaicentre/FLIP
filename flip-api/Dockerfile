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

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client

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
RUN chown -R ${UNAME}:${UNAME} /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
# Pre-create trust cert directory with correct ownership so the non-root user
# can write the CA cert fetched from Secrets Manager at runtime.
RUN mkdir -p /etc/ssl/trust && chown ${UNAME}:${UNAME} /etc/ssl/trust
WORKDIR /app
USER ${UNAME}

# Sync dependencies using uv
RUN uv sync

# Add fastapi executable to path
ENV PATH="/app/.venv/bin:$PATH"

# Expose the application port
EXPOSE 8000
EXPOSE 8080
EXPOSE 5678

# Run FastAPI application using uvicorn via uv, but wait before starting to allow the database to be ready
CMD ["/app/entrypoint.sh"]

FROM development AS production

COPY --chown=${UNAME}:${UNAME} src/ /app/src/

CMD ["/app/entrypoint.sh"]
