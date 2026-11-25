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
# XNAT Container Service image for the nifti_to_seg command (FLIP#1945): the NIfTI label masks an FL
# job stores on a scan (flip.add_resource) -> a DICOM-SEG ROI collection, which the XNAT OHIF viewer
# draws. Launched by the ResourceEvent subscription configure-nifti-to-seg.sh registers.
#
# NIFTI_TO_SEG_VERSION is the image tag the other copies must match: the publish workflow
# (.github/workflows/docker_build_xnat_nifti_to_seg.yml) pushes
# ghcr.io/londonaicentre/xnat-nifti-to-seg:<version>, and nifti_to_seg_command.json must name that
# exact string, and pyproject.toml's version must match too (all pinned by
# trust/xnat/tests/test_nifti_to_seg_image_pin.py). Bump all three with any change here.
# The package must be **public**: the Container Service pulls with no credentials.

FROM python:3.13-slim-bookworm

ARG NIFTI_TO_SEG_VERSION=1.0.0

LABEL org.opencontainers.image.source="https://github.com/londonaicentre/FLIP" \
      org.opencontainers.image.description="FLIP XNAT Container Service command: NIfTI masks to DICOM-SEG \
(see trust/xnat/nifti-to-seg/)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${NIFTI_TO_SEG_VERSION}"

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY src/ ./src/

# Build-time smoke check: the module and every dependency it imports load on this base, and its
# XNAT client constructs.
RUN cd /app/src && /app/.venv/bin/python -c "import nifti_to_seg; nifti_to_seg.Xnat('http://smoke', 'u', 'p')"

ENV PATH="/app/.venv/bin:${PATH}" \
    HOME=/tmp

# The Container Service invokes `python /app/src/nifti_to_seg.py ...` directly (see
# nifti_to_seg_command.json) — no ENTRYPOINT. Like xnat-dcm2niix it runs as xnat-web's uid
# (XNAT_CONTAINER_UID in trust/xnat/Makefile), so its output lands with the owner everything
# else under xnat-data has, and it does not run as root beside host mounts.
USER 1001:1001
CMD ["python", "/app/src/nifti_to_seg.py", "--help"]
