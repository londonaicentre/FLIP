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

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["Health"])

# The service is a uv "virtual" project (never installed as a distribution), so the
# only version source shared by the repo checkout and the container image is the
# pyproject.toml that sits next to the package (/app in the image).
_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


@lru_cache(maxsize=1)
def _service_version() -> str | None:
    """Look up the service version from the adjacent pyproject.toml.

    Returns:
        str | None: The ``[project].version`` value, or None when the file is
        missing or unparsable.
    """
    try:
        with _PYPROJECT_PATH.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None


@router.get("")
async def health_check() -> dict[str, str | None]:
    """
    Health check endpoint for the Data Access API

    Returns:
        dict[str, str | None]: The status of the service and the build it runs — the
        CI-baked ``FLIP_RELEASE`` image tag (v<X.Y.Z> or sha-<short7>, FLIP#1204), or the
        pyproject version for a local build that carries none.
    """
    return {"status": "ok", "version": os.environ.get("FLIP_RELEASE") or _service_version()}
