#!/usr/bin/env python3
#
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

"""Reading the trust kit file, shared by the renderer and the kit sync.

Same parsing rule as ``trust/deploy/helm/sync_k8s_kit.py``: ``KEY=value`` lines, comments
and blanks skipped, surrounding quotes stripped. Keeping one copy means a kit value that
renders one way into a spec cannot reach a SECRET object another way.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The ``PROD`` -> kit-file token map lives in ``deploy/env_mode.mk`` and nowhere else.
#: Scripts here take the resolved token as ``--env``; the Makefile injects it.
DEFAULT_ENV = "development"


def read_env_vars(env_path: Path) -> dict[str, str]:
    """Read ``KEY=value`` pairs from a kit file, stripping surrounding quotes."""
    pairs: dict[str, str] = {}
    if not env_path.exists():
        return pairs
    with open(env_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            pairs[key.strip()] = value.strip().strip('"').strip("'")
    return pairs


def is_placeholder(value: str) -> bool:
    """True if a kit value is empty or still a scaffolding placeholder (``<...>``)."""
    v = (value or "").strip()
    return not v or (v.startswith("<") and v.endswith(">"))


def resolve_env(env: str | None) -> str:
    """The kit-file env token, refusing to guess when ``PROD`` is set without ``--env``.

    Two copies of the PROD map is how the LZA tokens drifted out of step, so this script
    keeps none: with ``PROD`` set it insists on being run through the Makefile.
    """
    if env:
        return env
    prod = os.environ.get("PROD", "")
    if prod:
        sys.exit(
            f"❌ PROD={prod!r} is set but --env is not.\n"
            "   PROD maps to the kit-file token in deploy/env_mode.mk only. Run this through\n"
            "   the Snowflake Makefile, which injects it:\n"
            f"       make -C trust/deploy/snowflake <target> KIT=<CODE> PROD={prod}\n"
            "   Or pass the token directly:  --env <token>"
        )
    return DEFAULT_ENV


def kit_path(repo_root: Path, code: str, env: str) -> Path:
    return repo_root / "trust" / f".env.{code}.{env}"
