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

"""This service's own version, shared by the /health route and the health collector."""

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from trust_api.utils.logger import logger

# The service is a uv "virtual" project (never installed as a distribution), so the
# only version source shared by the repo checkout and the container image is the
# pyproject.toml at the package root (/app in the image).
_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Baked into every CI-published image (Dockerfile ARG → ENV, FLIP#1204): the release tag
# (v<X.Y.Z>) or the sha-<short7> tag the build pushed. Empty in a local build.
_RELEASE_ENV = "FLIP_RELEASE"


@lru_cache(maxsize=1)
def service_version() -> str | None:
    """Look up the service version from the project's pyproject.toml.

    Returns:
        str | None: The ``[project].version`` value, or None when the file is
        missing or unparsable — which means a broken image layout, so it is logged
        (once, thanks to the cache) rather than silently rendering as "—" forever.
    """
    try:
        with _PYPROJECT_PATH.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
        logger.warning(f"Could not read the service version from {_PYPROJECT_PATH}: {type(e).__name__}: {e}")
        return None


def build_identity() -> str | None:
    """Name the build this container runs, for /health and the hub heartbeat.

    Prefers the CI-baked ``FLIP_RELEASE`` image tag: two different builds can share one
    pyproject version (it is bumped only when the service has user-visible changes), so
    the pyproject number cannot tell an operator which build a site is on — the image tag
    can. Read from the environment on every call (not cached) so tests and a
    ``docker exec`` override behave as expected.

    Returns:
        str | None: ``FLIP_RELEASE`` when set and non-empty, else the pyproject
        ``[project].version``, else None when neither is available.
    """
    return os.environ.get(_RELEASE_ENV) or service_version()
