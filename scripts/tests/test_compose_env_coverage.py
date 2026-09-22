#!/usr/bin/env python3
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
"""Every variable a dev compose interpolates without a default is declared in the env example (FLIP#1230).

Compose renders an unset ``${VAR}`` as an empty string, not an absent one, so a variable that
drops out of ``.env.development.example`` keeps "working" for every existing checkout (their
``.env.development`` still carries it) and breaks only fresh clones — as ``MIN_CLIENTS`` did
(since removed outright): dropped from the example, still passed verbatim to the FL server,
parsed as ``""`` inside the job process, never reported to the hub.

``scripts/check_env_vars.py`` guards the other direction (example → developer's file). This
guard closes the loop: compose → example. A variable is exempt when the compose gives it a
default (``${VAR:-x}`` / ``${VAR-x}``) or when the Makefiles export it rather than the env file.

Deliberately stdlib-only and executable as a plain script — ``test_trust_kit_scripts.yml``
runs these with ``python <file>``, not pytest. Line-based, like ``test_container_identity.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_EXAMPLE = REPO_ROOT / ".env.development.example"

# The hub's dev composes. Trust composes take their variables from the per-trust kit
# (``--env-file trust/.env.<CODE>.<env>``) plus a hub-shared block the trust Makefile
# exports, so they are a different contract and are not checked here.
DEV_COMPOSE_FILES = (
    "deploy/compose.development.yml",
    "deploy/compose.development.nvflare.yml",
    "deploy/compose.development.flower.yml",
    "deploy/compose.development.debug.override.yml",
)

# Interpolated by the composes but supplied by make, not the env file:
# deploy/fl_backend.mk (`export DOCKER_FL_* FL_PROVISIONED_DIR FL_JOBS_DIR`),
# deploy/instance.mk (`export FLIP_INSTANCE`), Makefile (`export DOCKER_GID`, UID/GID).
MAKE_PROVIDED = frozenset(
    {
        "DOCKER_FL_API_NAME",
        "DOCKER_FL_SERVER_NAME",
        "DOCKER_FL_CLIENT_NAME",
        "FL_PROVISIONED_DIR",
        "FL_JOBS_DIR",
        "DOCKER_FL_REGISTRY",
        "FLIP_INSTANCE",
        "DOCKER_GID",
        "UID",
        "GID",
    }
)

# ${VAR} only — a modifier (``:-``, ``-``, ``:?``, ``?``, ``:+``, ``+``) makes it self-sufficient.
BARE_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Same regex as scripts/check_env_vars.py: a real assignment, not a commented-out one.
ENV_ASSIGNMENT = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)


def declared_in_example() -> set[str]:
    return set(ENV_ASSIGNMENT.findall(ENV_EXAMPLE.read_text()))


def bare_interpolations(path: Path) -> dict[str, int]:
    """Map each bare ``${VAR}`` in ``path`` to the first line it appears on (comments skipped)."""
    found: dict[str, int] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for name in BARE_INTERPOLATION.findall(line):
            found.setdefault(name, n)
    return found


def check_dev_composes_are_covered(failures: list[str]) -> None:
    declared = declared_in_example()
    for rel in DEV_COMPOSE_FILES:
        path = REPO_ROOT / rel
        used = bare_interpolations(path)
        if not used:
            raise ValueError(f"{rel}: no ${{VAR}} interpolations found — parser broken or file moved")
        for name, line in sorted(used.items()):
            if name in declared or name in MAKE_PROVIDED:
                continue
            failures.append(
                f"{rel}:{line}: interpolates ${{{name}}} with no default, but {ENV_EXAMPLE.name} does not "
                f"declare it. A fresh clone renders it as an empty string. Either add `{name}=` to the "
                f"example, give the compose a default (`${{{name}:-…}}`), or — if a Makefile exports it — "
                f"add it to MAKE_PROVIDED here."
            )


def main() -> int:
    failures: list[str] = []
    try:
        check_dev_composes_are_covered(failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ compose env coverage guard failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ compose env coverage guard passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
