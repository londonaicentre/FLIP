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
"""The FL API's own version, for /health and the OpenAPI document.

Identical in both backends' fl-api. Mirrors ``flip_api.utils.version``.
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from fl_api.utils.logger import logger

_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"

_RELEASE_ENV = "FLIP_RELEASE"


@lru_cache(maxsize=1)
def service_version() -> str | None:
    """Look up the service version from the project's pyproject.toml.

    Returns:
        str | None: The ``[project].version`` value, or None when the file is missing or
        unparsable — a broken image layout, logged once thanks to the cache.
    """
    try:
        with _PYPROJECT_PATH.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as e:
        logger.warning(f"Could not read the service version from {_PYPROJECT_PATH}: {type(e).__name__}: {e}")
        return None


def build_identity() -> str | None:
    """Name the build this container runs.

    Prefers the CI-baked ``FLIP_RELEASE`` image tag: two different builds can share one
    pyproject version, so only the image tag tells them apart. Read from the environment
    on every call (not cached).

    Returns:
        str | None: ``FLIP_RELEASE`` when set and non-empty, else the pyproject
        ``[project].version``, else None.
    """
    return os.environ.get(_RELEASE_ENV) or service_version()
