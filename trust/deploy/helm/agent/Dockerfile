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

# The in-cluster auto-upgrade agent (#1644): the tick script, plus the two binaries it drives.
#
# Nothing is fetched at run time — the chart's CronJob is the thing that upgrades a site, so a
# run-time download would fail exactly when the site is behind and the registry is unreachable. The
# versions are pinned here and verified against the sums the upstream project publishes beside each
# tarball (an integrity check on the transfer, not a substitute for pinning the version).

FROM alpine:3.21 AS fetch
ARG HELM_VERSION=3.17.3
ARG KUBECTL_VERSION=1.31.4
ARG TARGETARCH=amd64

RUN apk add --no-cache curl ca-certificates

WORKDIR /out
RUN set -eux; \
    # Keep upstream's file name: its .sha256sum lists the hash against that name, and sha256sum -c
    # reads the name from the file rather than from our argument.
    TARBALL="helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    curl -fsSLo "$TARBALL" "https://get.helm.sh/$TARBALL"; \
    curl -fsSLo "$TARBALL.sha256sum" "https://get.helm.sh/$TARBALL.sha256sum"; \
    sha256sum -c "$TARBALL.sha256sum"; \
    tar -xzf "$TARBALL" -C . "linux-${TARGETARCH}/helm"; \
    mv "linux-${TARGETARCH}/helm" /usr/local/bin/helm; \
    curl -fsSLo kubectl "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl"; \
    curl -fsSLo kubectl.sha256 "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256"; \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c -; \
    mv kubectl /usr/local/bin/kubectl; \
    chmod +x /usr/local/bin/helm /usr/local/bin/kubectl

FROM alpine:3.21
RUN apk add --no-cache ca-certificates python3
COPY --from=fetch /usr/local/bin/helm /usr/local/bin/helm
COPY --from=fetch /usr/local/bin/kubectl /usr/local/bin/kubectl
# The tick, as it runs on a host. Same file, same protocol; --mode cluster differs only in what
# "current release" and "upgrade" mean.
# The parent is created first, and deliberately: COPY --chmod also applies to the directories it
# has to create, so an implicit /opt/flip would end up without its execute bit and nothing outside
# root could reach the file inside it.
RUN mkdir -p /opt/flip
# 0444, not 0400: the container runs as uid 65534, and a root-owned 0400 file is unreadable to it.
COPY --chmod=0444 scripts/trust_upgrade_agent.py /opt/flip/trust_upgrade_agent.py

# The chart's runAsUser is 65534, which Alpine already owns as `nobody` — no account to add, and
# the agent never needs a shell or a writable root.
USER 65534:65534
WORKDIR /tmp

ENTRYPOINT ["python3", "/opt/flip/trust_upgrade_agent.py"]
CMD ["--help"]
