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
# XNAT Container Service image for the export_mask command (FLIP#55): an OHIF DICOM-SEG
# assessor -> NIfTI, uploaded back to the assessor. Registered only on MONAI Label trusts
# (configure-export-mask.sh).
#
# EXPORT_MASK_VERSION is the image tag the other copies must match: the publish workflow
# (.github/workflows/docker_build_xnat_export_mask.yml) pushes
# ghcr.io/londonaicentre/xnat-export-mask:<version>, and export_mask_command.json must name that
# exact string — XNAT finds the command by it, so a bumped image with a stale command is never
# triggered — and pyproject.toml's version must match too (both pinned by
# trust/xnat/tests/test_export_mask_image_pin.py). Bump all three with any change here.
# The package must be **public**: the Container Service pulls with no credentials.

FROM python:3.12-slim-bookworm

ARG EXPORT_MASK_VERSION=1.0.0

LABEL org.opencontainers.image.source="https://github.com/londonaicentre/FLIP" \
      org.opencontainers.image.description="FLIP XNAT Container Service command: OHIF DICOM-SEG to NIfTI \
(see trust/xnat/export-mask/)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${EXPORT_MASK_VERSION}"

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY src/ ./src/

# Build-time smoke check: the module and every dependency it imports load on this base, and its
# XNAT client constructs. Constructing it is the point: pyxnat, which this replaced, imported
# undeclared packages only when its client was built, so an import alone passed while every
# Container Service run failed.
RUN cd /app/src && /app/.venv/bin/python -c "import export_mask; export_mask.Xnat('http://smoke', 'u', 'p')"

ENV PATH="/app/.venv/bin:${PATH}" \
    HOME=/tmp

# The Container Service invokes `python /app/src/export_mask.py ...` directly (see
# export_mask_command.json) — no ENTRYPOINT. Like xnat-dcm2niix it runs as xnat-web's uid
# (XNAT_CONTAINER_UID in trust/xnat/Makefile), so its output lands with the owner everything
# else under xnat-data has, and it does not run as root beside host mounts.
USER 1001:1001
CMD ["python", "/app/src/export_mask.py", "--help"]
