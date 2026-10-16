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
"""Guards that Flower's per-run dependency install stays off on every compose, Helm and image shape (FLIP#1418).

Flower can ``uv sync`` an app's ``pyproject.toml`` into a per-run environment at every app launch.
FLIP turns that off on both sides because it failed two ways: a cold sync outlives Flower's task
heartbeat, so the run never starts, and every ClientApp launch in a run shares one environment that
each launch deletes on exit, so later rounds fail. Each half is one setting per launch definition,
across every compose file, the Helm chart, the FL images and the ECS task, and re-enabling it on
one shape breaks only that shape, intermittently, in a way that looks like a flaky run rather than
a config change:

- Nothing may pass ``--allow-runtime-dependency-installation``. Off is the SuperNode default, so the
  absence of the flag is the whole of the setting there; and a SuperLink still honours the flag
  (deprecated, but it overrides the variable below).
- Every SuperLink must set ``FLWR_DISABLE_RUNTIME_DEPENDENCY_INSTALLATION=1``. On is the SuperLink
  default, and flwr compares the value with the string ``"1"``, so ``=true`` or ``=yes`` would leave
  the install on with nothing in the file looking wrong. flwr also accepts a
  ``--disable-runtime-dependency-installation`` flag to the same effect; FLIP uses the variable as its
  one switch, so this guard asks for the variable.

The ECS SuperLink is held to the same rule by
``deploy/providers/AWS/tests/test_flower_superlink_runtime_install.py``, next to the Terraform it reads.

Deliberately stdlib-only and executable as a plain script, like the other guards here —
``test_trust_kit_scripts.yml`` runs these with ``python <file>``, not pytest.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from test_container_identity import REPO_ROOT, Service, parse_services

SUPERNODE_FLAG = "--allow-runtime-dependency-installation"
SUPERLINK_ENV = "FLWR_DISABLE_RUNTIME_DEPENDENCY_INSTALLATION"

# Every file that can put a flag on a SuperNode's or SuperLink's command line: the composes, the
# chart (templates, helpers and values), and the FL images' Dockerfiles, whose ENTRYPOINT every
# compose `command:` and Helm `args:` is appended to. Globs rather than a list, so a new compose,
# chart template or image is covered without anyone remembering to add it here.
FLAG_SCAN_GLOBS = (
    "deploy/compose*.yml",
    "trust/deploy/compose*.yml",
    "fl-services/flower/compose*.yml",
    "trust/deploy/helm/templates/*",
    "trust/deploy/helm/values*.yaml",
    "fl-services/flower/*/Dockerfile",
)

# Every compose file that can run a Flower SuperLink; each SuperLink found in any of them is checked.
# The NVFLARE composes are left out because they name their own server through the same
# DOCKER_FL_SERVER_NAME variable.
FLOWER_COMPOSE_GLOBS = (
    "deploy/compose*flower*.yml",
    "fl-services/flower/compose*.yml",
)

# The composes known to run a Flower SuperLink today. Each must still yield at least one, or the
# check below would pass by asserting over nothing after a rename.
SUPERLINK_COMPOSES = (
    "deploy/compose.development.flower.yml",
    "deploy/compose.production.flower.yml",
    "fl-services/flower/compose.dev.yml",
    "fl-services/flower/compose.secure.yml",
)

# The SuperLink image: named by variable in the hub composes (deploy/fl_backend.mk resolves it to
# flower-superlink for FL_BACKEND=flower), literally in the standalone harness.
SUPERLINK_IMAGE_MARKERS = ("${DOCKER_FL_SERVER_NAME}", "flower-superlink")

# `- NAME=value` (list form) or `NAME: value` (map form); the value stops at whitespace or a YAML comment.
ENV_LINE = re.compile(rf"^\s*(?:-\s*)?{SUPERLINK_ENV}\s*[=:]\s*([^\s#]*)")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "{{/*", "{{- /*"))


def _image(svc: Service) -> str | None:
    for line in svc.lines:
        m = re.match(r"\s{4}image:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


def _registers_keys(svc: Service) -> bool:
    return any(re.match(r"\s{4}entrypoint:.*register-supernode-keys", line) for line in svc.lines)


def superlinks(path: Path) -> list[Service]:
    """The services in ``path`` that run the SuperLink binary.

    The key-registration services reuse the SuperLink image with an ``entrypoint:`` that runs
    ``register-supernode-keys.sh`` and no app, so they are not SuperLinks for this purpose. Any other
    entrypoint override still counts: it may well wrap ``flower-superlink``.
    """
    found: list[Service] = []
    for svc in parse_services(path):
        image = _image(svc)
        if image is None or not any(marker in image for marker in SUPERLINK_IMAGE_MARKERS):
            continue
        if _registers_keys(svc):
            continue
        found.append(svc)
    return found


def check_nothing_passes_the_install_flag(failures: list[str]) -> None:
    """No compose file, chart file or FL image may pass the opt-in flag outside a comment."""
    for glob in FLAG_SCAN_GLOBS:
        paths = sorted(p for p in REPO_ROOT.glob(glob) if p.is_file())
        if not paths:
            failures.append(f"{glob}: matched no files — the scan below would pass by reading nothing")
            continue
        for path in paths:
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if SUPERNODE_FLAG in line and not _is_comment(line):
                    failures.append(
                        f"{path.relative_to(REPO_ROOT)}:{n}: passes {SUPERNODE_FLAG}, which turns the per-run "
                        f"install on: on a SuperNode every ClientApp launch in a run shares one per-run env that "
                        f"each launch deletes on exit, and on a SuperLink it overrides {SUPERLINK_ENV}. Drop the "
                        f"flag; the apps run in the image's environment (FLIP#1418)."
                    )


def check_every_superlink_disables_runtime_install(failures: list[str]) -> None:
    """Every Flower SuperLink service sets the disable variable, to exactly ``1``."""
    found: list[Service] = []
    for rel in SUPERLINK_COMPOSES:
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: expected compose file is missing")
        elif not superlinks(path):
            failures.append(
                f"{rel}: found no SuperLink service (an `image:` naming one of {SUPERLINK_IMAGE_MARKERS} "
                f"that does not run register-supernode-keys.sh). If the file's shape changed, update this "
                f"guard rather than let it pass over nothing."
            )
    for glob in FLOWER_COMPOSE_GLOBS:
        for path in sorted(REPO_ROOT.glob(glob)):
            found.extend(superlinks(path))
    for svc in found:
        values = []
        for line in svc.lines:
            m = ENV_LINE.match(line)
            if m and not _is_comment(line):
                values.append(m.group(1).strip("\"'"))
        if not values:
            failures.append(
                f"{svc.where}: does not set {SUPERLINK_ENV}. The SuperLink runtime-installs by default, "
                f"and a cold per-run `uv sync` outlives Flower's task heartbeat, so the run never starts. "
                f"Add `- {SUPERLINK_ENV}=1` to its environment (FLIP#1418)."
            )
        elif values != ["1"]:
            failures.append(
                f"{svc.where}: sets {SUPERLINK_ENV} to {values}. flwr disables the install only when the "
                f'value is exactly the string "1"; anything else leaves it on. Set it once, to 1 (FLIP#1418).'
            )


def main() -> int:
    failures: list[str] = []
    try:
        check_nothing_passes_the_install_flag(failures)
        check_every_superlink_disables_runtime_install(failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ Flower runtime-install guards failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ Flower runtime-install guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
