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

# MONAI Label server for AI-assisted annotation in the XNAT OHIF viewer.
#
# Base has Blackwell support (sm_120 kernels) and works on hosts with driver CUDA 12.7 and
# 12.9 thanks to CUDA 12.x minor-compat. PyTorch container 25.04 is based on 2.7.0a0.
FROM nvcr.io/nvidia/pytorch:25.04-py3

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# Build a constraints file that ONLY pins the torch stack, so the installs below cannot
# swap out the CUDA-matched torch that ships in this base image.
RUN python - <<'PY' > /tmp/torch_constraints.txt
import importlib
for m in ("torch","torchvision","torchaudio"):
    try: print(f"{m}=={importlib.import_module(m).__version__}")
    except Exception: pass
PY
RUN echo "Torch constraints:" && cat /tmp/torch_constraints.txt

# MONAI Label is installed from a pinned git commit, NOT from PyPI.
#
# The latest release (0.8.5, Nov 2024) omits `auth=self.auth` in
# XNATDatastore._request_get(), so every read against the XNAT REST API is unauthenticated.
# Opening the MONAI Label panel in the XNAT OHIF viewer then fails with HTTP 500 over an
# underlying 401 (Project-MONAI/MONAILabel#1837). The fix is PR #1838, merged 2025-06-17
# and still unreleased.
#
# 3806289 is that merge commit, and remains the most recent commit touching xnat.py — so it
# carries every XNAT datastore fix to date while staying as close to the last release as
# possible. Re-check https://github.com/Project-MONAI/MONAILabel/releases before bumping:
# once a release after 0.8.5 exists, this can go back to a plain version pin.
ARG MONAILABEL_REF=38062894051d6fd1d3ba72cb2f8a39ad4f66f535

# IMPORTANT: temporarily clear NVIDIA's global constraints so pip can upgrade transitive deps
# (urllib3, httpx, ...) while honouring ONLY the torch pins above.
# git is needed because MONAI Label derives its version with versioneer from repo metadata.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && PIP_CONSTRAINT= pip install -U -c /tmp/torch_constraints.txt \
      "pydicom<3" \
      monai \
      "monailabel @ git+https://github.com/Project-MONAI/MONAILabel.git@${MONAILABEL_REF}" \
      uvicorn[standard] fastapi python-multipart

# Fail the build rather than ship the 401 bug: the fixed _request_get passes auth through.
RUN python -c "\
import inspect, sys; \
from monailabel.datastore.xnat import XNATDatastore as X; \
src = inspect.getsource(X._request_get); \
sys.exit(0) if 'auth=self.auth' in src else sys.exit('MONAI Label lacks the XNAT auth fix (#1838)')"

# Copy the radiology app out of the installed package at build time (this is a local copy from
# the wheel's sample-apps, not a download) so a cold start does not depend on it. Pretrained
# model weights are still fetched at server start; the compose overlay persists them.
RUN monailabel apps --name radiology --download --output /workspace/app

COPY entrypoint.sh /workspace/entrypoint.sh
ENTRYPOINT ["/bin/bash", "/workspace/entrypoint.sh"]
