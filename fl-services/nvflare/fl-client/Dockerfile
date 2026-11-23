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
# TotalSegmentator's weights, built by fl-services/nvflare/totalseg-weights/Dockerfile (FLIP#1945).
ARG TOTALSEG_WEIGHTS_REF=ghcr.io/londonaicentre/totalsegmentator-weights:latest
FROM ${TOTALSEG_WEIGHTS_REF} AS totalseg-weights
FROM ${BASE_REF} AS base

# What the client has and the server does not: flip-utils' `segmentation` extra, so a job at a trust
# can run TotalSegmentator (FLIP#1945). The server never predicts, so fl-base stays without it.
FROM base AS env
ARG UNAME=flip
# The same switch fl-base takes. The sync removes whatever it does not ask for, so it must ask for
# the dev group when the base was built with it.
ARG LOCAL_DEV=false
# Where TotalSegmentator looks for its weights. Outside $HOME, which the dev composes re-point.
ENV TOTALSEG_HOME_DIR=/opt/totalsegmentator
# fl-base left /app holding flip-utils' pyproject.toml and uv.lock, so this needs nothing from the
# build context. It runs as the user that owns the venv, so no chown follows (a recursive one would
# copy the whole venv into this layer, see fl-base/Dockerfile).
USER ${UNAME}
RUN uv sync --frozen --no-cache --compile-bytecode --extra nvflare --extra full --extra segmentation $(if [ "$LOCAL_DEV" = "true" ]; then echo "--dev"; else echo "--no-dev"; fi)
# A job cannot download at a trust (FLIP#1206), so the weights are in the image: owned by root and
# read-only to the job. TotalSegmentator rewrites config.json on every prediction when it can, with
# no lock, so two jobs in one client could corrupt it; it carries on when it cannot.
COPY --from=totalseg-weights /opt/totalsegmentator ${TOTALSEG_HOME_DIR}
USER root

FROM env AS development

# Names the non-root user created in fl-base (GHSA-8465), used only for the
# COPY --chown and USER below. Fixed at `flip` and no longer supplied per-build
# (FLIP#1171) — see fl-base/Dockerfile. UID/GID are not declared here because
# nothing in this file references them; the runtime uid is re-pointed by `user:`
# in the composes, which is what has to match the host-owned bind mounts.
ARG UNAME=flip

# Copy entrypoint script, and the one-shot init that does every write into the kit (FLIP#1570)
COPY --chown=${UNAME}:${UNAME} fl-client/entrypoint.sh /app/entrypoint.sh
COPY --chown=${UNAME}:${UNAME} fl-client/init.sh /app/fl-client-init.sh

# Make entrypoint and init scripts executable
RUN chmod +x /app/entrypoint.sh /app/fl-client-init.sh

# Expose necessary ports
EXPOSE 8081 8082

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
