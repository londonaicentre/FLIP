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
# Pinned dcm2niix image for the XNAT Container Service (FLIP#980).
#
# Replaces Docker Hub's `xnat/dcm2niix:latest`, which resolves to a 2021 build
# (v1.0.20211006) that upstream no longer updates and that silently drops slices
# from valid series ("Slices skipped: image position not sequential" mis-fires on
# clean, uniformly-spaced synthesized CT series — every affected spleen study then
# fails the tutorials' image/label shape QC).
#
# Referenced by exact image string in three places — keep them in sync when
# bumping DCM2NIIX_VERSION:
#   - trust/xnat/xnat/config/dcm2niix_command.json           (Compose deploy)
#   - deploy/providers/kubernetes/templates/xnat-init-job.yaml (inline copy)
#   - trust/imaging-api/imaging_api/config.py                 (Settings.DCM2NIIX_IMAGE)
# The publish workflow (.github/workflows/docker_build_xnat_dcm2niix.yml) reads
# DCM2NIIX_VERSION below and pushes ghcr.io/londonaicentre/xnat-dcm2niix:<version>.

FROM debian:13-slim

# The source label associates the GHCR package with this repo, so it is listed
# under github.com/londonaicentre/FLIP/packages alongside the other FLIP images
# instead of standing alone under the org. The siblings inherit that link from
# having been first published by a GITHUB_TOKEN push from this repo's workflows;
# the label states it explicitly, so the link holds however the image is pushed.
LABEL org.opencontainers.image.source="https://github.com/londonaicentre/FLIP" \
      org.opencontainers.image.description="dcm2niix for the FLIP trust XNAT Container Service, pinned to a \
current upstream release (see trust/xnat/dcm2niix/)" \
      org.opencontainers.image.licenses="Apache-2.0"

# Upstream release binary (glibc-linked), pinned by version AND checksum so a
# retagged upstream asset cannot slip through.
ARG DCM2NIIX_VERSION=v1.0.20260724
ARG DCM2NIIX_SHA256=2916a7bc64511eb20fb976a994799183cc1bfdd03395bf5d8edef54a58dee06f

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -fsSL -o /tmp/dcm2niix_lnx.zip \
        "https://github.com/rordenlab/dcm2niix/releases/download/${DCM2NIIX_VERSION}/dcm2niix_lnx.zip" \
    && echo "${DCM2NIIX_SHA256}  /tmp/dcm2niix_lnx.zip" | sha256sum -c - \
    && unzip -o /tmp/dcm2niix_lnx.zip -d /usr/local/bin dcm2niix \
    && chmod 0755 /usr/local/bin/dcm2niix \
    && rm /tmp/dcm2niix_lnx.zip \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    # Build-time smoke check: the binary runs on this base (glibc) and reports the
    # pinned version. Checked via the pipe because dcm2niix itself exits 3 on
    # --version; the pipeline's status is grep's.
    && dcm2niix --version | grep -qF "${DCM2NIIX_VERSION}"

# The Container Service invokes `dcm2niix ... -o /output /input` directly (see
# dcm2niix_command.json) — no ENTRYPOINT, and the image keeps the root default
# user to match the previous image's output-file ownership on the build mount.
CMD ["dcm2niix", "--version"]
