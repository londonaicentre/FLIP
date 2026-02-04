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

# Use a configurable base image
ARG BASE_REF=ghcr.io/londonaicentre/flare-fl-base:latest
FROM ${BASE_REF} AS base

FROM base AS development

# Copy entrypoint script
COPY fl-client/entrypoint.sh /app/entrypoint.sh

# Make entrypoint script executable
RUN chmod +x /app/entrypoint.sh

# Expose necessary ports
EXPOSE 8081 8082

# Use ENTRYPOINT instead of CMD for better signal handling
ENTRYPOINT ["/app/entrypoint.sh"]

FROM development AS production

# Override entrypoint and command for production
# Use CMD instead of ENTRYPOINT for production to allow better orchestration
CMD ["/app/entrypoint.sh"]
