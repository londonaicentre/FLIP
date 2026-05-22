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

ARG NGINX_VERSION
FROM nginx:${NGINX_VERSION}

# Copy your custom nginx.conf into the correct location
# This will overwrite the nginx.conf included in the base image
COPY custom-nginx.conf /etc/nginx/nginx.conf

# Ensure correct permissions (chmod is done during build as root before switching to nginx user)
RUN chmod 644 /etc/nginx/nginx.conf

# Drop root privileges: the nginx user exists in the base image.
# Requires NET_BIND_SERVICE capability to bind to port 80 (added in compose).
USER nginx
