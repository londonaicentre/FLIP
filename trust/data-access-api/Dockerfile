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

ARG UID
ARG GID
ARG UNAME
ARG DEBUG_PORT
RUN groupadd -g ${GID} ${UNAME}
# Create a user with the same UID and GID as the host user with home directory at /home/${UNAME}
RUN useradd -u ${UID} -g ${GID} -m -d /home/${UNAME} -s /bin/bash ${UNAME}

# Add uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Sync the project into a new environment, using the frozen lockfile
COPY pyproject.toml uv.lock ./

RUN chown -R ${UNAME}:${UNAME} /app
COPY ./entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
USER ${UNAME}
RUN uv sync --frozen

# Add fastapi executable to path
ENV PATH="/app/.venv/bin:$PATH"

# Expose the application port
EXPOSE 8000
EXPOSE ${DEBUG_PORT}

# Run FastAPI application using uvicorn via uv
CMD ["./entrypoint.sh"]

FROM development AS production

COPY --chown=${UNAME}:${UNAME} . .
RUN chmod +x /app/entrypoint.sh
CMD ["./entrypoint.sh"]
