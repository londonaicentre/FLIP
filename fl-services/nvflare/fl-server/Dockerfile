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

# Use a configurable base image
ARG BASE_REF=ghcr.io/londonaicentre/flare-fl-base:latest
FROM ${BASE_REF} AS base

FROM base AS development

# Build args for the non-root user created in fl-base (GHSA-8465).
# The compose wires host UID/GID so the container user owns bind-mounted
# provision directories in dev. Prod defaults: UID 1000 / GID 1000 / flip.
ARG UID
ARG GID
ARG UNAME

# Copy entrypoint script
COPY --chown=${UNAME}:${UNAME} fl-server/entrypoint.sh /app/entrypoint.sh

# Make entrypoint script executable
RUN chmod +x /app/entrypoint.sh

# Expose necessary ports
EXPOSE 8002

# Switch to the non-root user created in fl-base (GHSA-8465)
USER ${UNAME}

# Use ENTRYPOINT instead of CMD for better signal handling
ENTRYPOINT ["/app/entrypoint.sh"]

FROM development AS production

# No USER/ARG re-declaration needed here: production is FROM development, which
# already switched to the non-root user (GHSA-8465) — re-asserting USER ${UNAME}
# would be a no-op inherited from that stage.

# Override entrypoint and command for production
# Use CMD instead of ENTRYPOINT for production to allow better orchestration
CMD ["/app/entrypoint.sh"]
