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

FROM python:3.12-slim-bookworm AS development

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 -m -d /home/app -s /bin/bash app

WORKDIR /app

# Add uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Sync the project into a new environment, using the frozen lockfile
COPY pyproject.toml ./

COPY ./entrypoint.sh /app/entrypoint.sh
RUN chown -R app:app /app

RUN chmod +x /app/entrypoint.sh

USER app

# Sync dependencies using uv
RUN uv sync --no-dev

# Add fastapi executable to path
ENV PATH="/app/.venv/bin:$PATH"

# Expose port (use 8000 since we dropped NGINX and aren't using a socket)
EXPOSE 8000
EXPOSE 5679

# Run FastAPI application using uvicorn via uv
CMD ["bash", "-c", "/app/entrypoint.sh"]

FROM development AS production
COPY --chown=app:app fl_api/ /app/fl_api/

CMD ["bash", "-c", "/app/entrypoint.sh"]