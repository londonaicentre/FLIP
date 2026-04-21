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
