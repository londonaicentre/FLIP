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

# Local development image — used by deploy/compose.development.yml with a bind
# mount of the repo into /app. Stag/prod do not run flip-ui as a container;
# the UI is served as a static bundle from S3 + CloudFront (see
# deploy/providers/AWS/cloudfront.tf and `make deploy-ui`).
#
# ⚠️  Do not use in production. This Dockerfile exists only to support the
# local Vite dev server. Stag/prod do not consume it (no GHCR publish, no
# remote pull) and any deployed FLIP environment serves the UI as static
# assets from S3 + CloudFront.
#
# This image runs as root. No USER directive is set, deliberately — the dev
# workflow bind-mounts the host repo into /app and runs `npm install` against
# it on container start (docker/ui-entrypoint.sh), which needs to write into
# the bind-mounted tree regardless of the host UID/GID. Matching host UID/GID
# at build time (as flip-api/Dockerfile does) was rejected because this
# Dockerfile is the shared artifact across every contributor's machine —
# baking in one UID/GID would force every contributor whose host IDs don't
# match to rebuild the image locally, defeating the point of having a single
# checked-in image definition. The blast radius is bounded: the container is
# dev-only, never reachable from stag/prod, and only ever runs the Vite dev
# server against a local checkout. Production has no flip-ui container at
# all — a compromise of this image cannot affect any deployed FLIP
# environment. Reassess if this image is ever repurposed for stag/prod or
# exposed beyond localhost.
FROM node:22-alpine

# Install dependencies for building node modules
RUN apk add --no-cache python3 make g++ xdg-utils

# Install bootstrap entrypoint outside /app so bind mounts don't replace it
COPY docker/ui-entrypoint.sh /usr/local/bin/ui-entrypoint.sh
RUN chmod +x /usr/local/bin/ui-entrypoint.sh

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

ARG UI_PORT
# Expose port
EXPOSE $UI_PORT

ENTRYPOINT ["/usr/local/bin/ui-entrypoint.sh"]

# Default command for development (used with volume mounts)
CMD ["npm", "run", "dev"]
