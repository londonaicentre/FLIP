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
# Flower fleet proxy (FLIP#1517): the SuperLink's only trust-facing listener. See
# templates/fleet-proxy.conf.template for why it exists and fleet-routes.map for what it lets through.

ARG NGINX_VERSION=1.30.1-alpine
FROM nginx:${NGINX_VERSION}

# Defaults fit the ECS task, where the proxy shares the SuperLink's network namespace;
# the compose stacks override the upstream (see their fleet-proxy services).
ENV FLEET_PROXY_LISTEN_PORT=9092 \
    FLEET_PROXY_UPSTREAM=127.0.0.1:8000 \
    FLEET_PROXY_UPSTREAM_TLS_NAME=localhost \
    FLEET_PROXY_RESOLVER=127.0.0.11 \
    FLEET_PROXY_CERTS_DIR=/certs \
    NGINX_ENVSUBST_FILTER=^FLEET_PROXY_ \
    NGINX_ENVSUBST_OUTPUT_DIR=/tmp

COPY nginx.conf /etc/nginx/nginx.conf
COPY fleet-routes.map /etc/nginx/fleet-routes.map
COPY templates/ /etc/nginx/templates/
COPY healthcheck.sh /usr/local/bin/fleet-proxy-healthcheck

# The entrypoint renders the template into /tmp (NGINX_ENVSUBST_OUTPUT_DIR), and every
# other writable path is under /tmp too, so the image needs no writable path of its own.
RUN rm -f /etc/nginx/conf.d/default.conf \
    && chmod 644 /etc/nginx/nginx.conf /etc/nginx/fleet-routes.map \
    && chmod 755 /usr/local/bin/fleet-proxy-healthcheck

# The SuperLink's own uid/gid (flwr/base's `app`), so the proxy reads the server key
# wherever the SuperLink can: the production harness's host-owned certs, with no compose
# `user:` override (scripts/tests/test_container_identity.py). Dev compose adds the host
# group instead, and on ECS the EFS access point maps every reader to its uid (1001, efs.tf).
USER 49999:49999
EXPOSE 9092

# Healthy only while a Fleet request gets through to the SuperLink: a proxy that cannot
# reach or verify it answers every SuperNode 503, which they retry forever, and the NLB's
# TCP check would still pass. Compose reads this; the ECS task restates it.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 CMD ["/usr/local/bin/fleet-proxy-healthcheck"]
