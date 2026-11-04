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
    curl -fsSLo helm.tgz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    curl -fsSLo helm.sha256 "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz.sha256sum"; \
    sha256sum -c helm.sha256; \
    tar -xzf helm.tgz -C . "linux-${TARGETARCH}/helm"; \
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
COPY --chmod=0400 scripts/trust_upgrade_agent.py /opt/flip/trust_upgrade_agent.py

# The chart's runAsUser is 65534, which Alpine already owns as `nobody` — no account to add, and
# the agent never needs a shell or a writable root.
USER 65534:65534
WORKDIR /tmp

ENTRYPOINT ["python3", "/opt/flip/trust_upgrade_agent.py"]
CMD ["--help"]
