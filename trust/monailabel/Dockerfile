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
# Slim base + CUDA wheels from PyTorch's own index, matching fl-services/*/fl-base. The
# obvious alternative — nvcr.io/nvidia/pytorch — is ~20GB because it bundles the full CUDA
# toolkit, TensorRT, DALI, Apex and JupyterLab, none of which this server runs; the cu128
# wheels carry the CUDA runtime libraries MONAI Label actually needs.
FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

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

# git: MONAI Label derives its version with versioneer from repo metadata.
# libgl1/libglib2.0-0: OpenCV, pulled in transitively, needs them at import time.
# curl: entrypoint.sh probes XNAT and the server's own readiness with it, and it is the first
#   tool anyone reaches for when debugging connectivity from inside the container. Unlike the
#   NVIDIA base this image used to be built on, python:slim does not ship it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install the torch stack FIRST, from PyTorch's CUDA-12.8 index only.
#
# --index-url (not --extra-index-url) is load-bearing. With --extra-index-url pip still sees
# PyPI, and PyPI's default torch is now a CUDA 13 build which outranks the cu128 wheel — that
# resolution pulls nvidia-*-cu13 and produces an image that cannot run here, because cu13
# requires NVIDIA driver >= 580 and the trust hosts run 575.x. Restricting the index to cu128
# removes the choice. cu128 also carries the Blackwell (sm_120) kernels the RTX 5090 needs.
RUN pip install --no-cache-dir torch torchvision \
      --index-url https://download.pytorch.org/whl/cu128

# Freeze exactly what that resolved, so the MONAI Label install below — whose dependency tree
# (monai, sam2, pytorch-ignite) also declares torch — cannot quietly swap it for a PyPI build.
RUN python - <<'PY' > /tmp/torch_constraints.txt
import importlib
for m in ("torch", "torchvision"):
    try:
        print(f"{m}=={importlib.import_module(m).__version__}")
    except Exception:
        pass
PY
RUN echo "Torch constraints:" && cat /tmp/torch_constraints.txt

RUN pip install --no-cache-dir -c /tmp/torch_constraints.txt \
      "pydicom<3" \
      monai \
      "monailabel @ git+https://github.com/Project-MONAI/MONAILabel.git@${MONAILABEL_REF}" \
      uvicorn[standard] fastapi python-multipart \
      --extra-index-url https://download.pytorch.org/whl/cu128

# Fail the build rather than ship a broken image. Two ways it silently breaks, neither of
# which surfaces until runtime:
#   1. the unreleased XNAT auth fix disappearing from the pinned ref;
#   2. pip resolving a CUDA 13 torch. PyPI serves cu13 by default, and cu13 requires NVIDIA
#      driver >= 580 while the trust hosts run 575.x — so it installs cleanly and then dies
#      at first inference with an opaque CUDA error.
#
# The wheel's local version tag (+cu128) is the signal to check. torch.cuda.get_arch_list()
# looks like the more direct test for Blackwell (sm_120) kernels, but it returns [] whenever
# torch.cuda.is_available() is false — which it always is in a GPU-less build — so it can
# only be used at runtime, not here.
RUN python -c "\
import inspect, sys, torch; \
from monailabel.datastore.xnat import XNATDatastore as X; \
v = torch.__version__; cuda = torch.version.cuda or ''; \
print('torch', v, 'cuda', cuda); \
'auth=self.auth' in inspect.getsource(X._request_get) or sys.exit('MONAI Label lacks the XNAT auth fix (#1838)'); \
cuda.startswith('12.') or sys.exit(f'torch is built for CUDA {cuda}; need 12.x (cu13 needs driver >=580)'); \
'+cu' in v or sys.exit(f'torch {v} is not a CUDA wheel')"

# Copy the radiology app out of the installed package at build time (this is a local copy from
# the wheel's sample-apps, not a download) so a cold start does not depend on it. Pretrained
# model weights are still fetched at server start; the compose overlay persists them.
RUN monailabel apps --name radiology --download --output /workspace/app

COPY entrypoint.sh /workspace/entrypoint.sh
ENTRYPOINT ["/bin/bash", "/workspace/entrypoint.sh"]
