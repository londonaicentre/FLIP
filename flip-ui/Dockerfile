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

# Development stage - for Docker Compose with volume mounts
FROM node:20.9.0-alpine AS development

# Install dependencies for building node modules
RUN apk add --no-cache python3 make g++ xdg-utils

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

ARG UI_PORT
# Expose port
EXPOSE $UI_PORT

# Default command for development (used with volume mounts)
CMD ["npm", "run", "dev"]

# Production stage - builds and serves the application
FROM development AS production

# Copy source files for production build
COPY . .

# TODO eventually the UI will be deployed in CloudFront so we won't need this section
CMD ["npm", "run", "dev"]
