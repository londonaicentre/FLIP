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
"""Guards on container identity in the compose files (FLIP#1171).

Build-time identity and runtime identity are two different things, and conflating them
is what made published FL images unrunnable for any developer whose host uid was not
1000. Each assertion here pins one half of that separation, and each corresponds to a
failure that actually happened rather than a hypothetical:

- A dev service that mounts the host-provisioned NVFLARE kit must run as the host uid,
  or the entrypoint's ``envsubst`` into the bind-mounted ``local/`` fails and the
  container crash-loops. This was missed once already, in the standalone harness
  (``fl-services/nvflare/compose.dev.yml``), which the deploy composes' fix did not
  reach.
- A service that overrides ``user:`` must also name that user, because the overridden
  uid is absent from the image's ``/etc/passwd``: ``pwd.getpwuid()`` raises, ``HOME``
  falls back to ``/``, and the symptom surfaces three layers away as torch's
  "Artifact of type=precompile already registered in mega-cache artifact factory".
- Production composes must not override ``user:`` at all: prod runs as the image's
  baked user, and EFS access points remap ownership regardless.

Deliberately stdlib-only and executable as a plain script — ``test_trust_kit_scripts.yml``
runs these with ``python <file>``, not pytest, and PyYAML is not installed there. The
parsing is therefore line-based rather than a real YAML load; it only needs to find
service boundaries and a handful of keys, and a compose file that defeats it would be
unusual enough to warrant a look anyway.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Services whose runtime uid must match the host user, identified by what they mount
# rather than by name — the point is the host-owned bind mount, not the service label.
PROVISIONED_KIT_MARKERS = ("workspace-dev/", "${FL_PROVISIONED_DIR}")

# ...and specifically by the container path they mount it AT, because only some of the
# kit is written. The entrypoints envsubst their generated config into `local/`, and
# NVFLARE stages job payloads through `transfer/`; `startup/` holds the certs and is
# only read. fl-api in the deploy composes mounts just `admin/local` and
# `admin/startup`, writes neither, and so legitimately runs as the image's own uid —
# whereas the standalone harness also gives it `admin/transfer`, which it does write.
KIT_WRITE_TARGETS = ("/app/local", "/app/transfer", "/app/admin/transfer")

DEV_COMPOSE_FILES = (
    "deploy/compose.development.nvflare.yml",
    "trust/deploy/compose_trust.development.nvflare.yml",
    "fl-services/nvflare/compose.dev.yml",
)

PROD_COMPOSE_GLOBS = (
    "deploy/compose.production*.yml",
    "trust/deploy/compose_trust.production*.yml",
)

IDENTITY_ENV_KEYS = ("HOME", "USER", "LOGNAME")


class Service:
    """One compose service: its name, the lines in its block, and the file it came from."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        self.lines: list[str] = []

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.name}"

    @property
    def user(self) -> str | None:
        for line in self.lines:
            m = re.match(r"\s{4}user:\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip("\"'")
        return None

    def writes_provisioned_kit(self) -> bool:
        """True if a host-provisioned kit path is mounted at somewhere this service writes."""
        for line in self.lines:
            stripped = line.lstrip()
            if not stripped.startswith("-"):
                continue
            if not any(marker in stripped for marker in PROVISIONED_KIT_MARKERS):
                continue
            # "- <source>:<target>[:<mode>]". Sources here contain ${VAR} but never a
            # colon, so a plain split is safe; drop a trailing access mode if present.
            parts = stripped[1:].strip().split(":")
            if len(parts) >= 3 and parts[-1] in {"ro", "rw", "z", "Z", "cached", "delegated"}:
                parts = parts[:-1]
            if len(parts) >= 2 and parts[-1] in KIT_WRITE_TARGETS:
                return True
        return False

    def env_keys(self) -> set[str]:
        keys = set()
        for line in self.lines:
            m = re.match(r"\s{6}-\s*([A-Za-z_][A-Za-z0-9_]*)=", line)
            if m:
                keys.add(m.group(1))
        return keys


def parse_services(path: Path) -> list[Service]:
    """Split a compose file into its top-level services.

    A service header is a two-space-indented ``name:`` after the ``services:`` key. Good
    enough for these files and avoids a PyYAML dependency CI does not have.
    """
    services: list[Service] = []
    current: Service | None = None
    in_services = False
    for line in path.read_text().splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if not in_services:
            continue
        if re.match(r"^\S", line):  # back to top level: services block is over
            in_services = False
            current = None
            continue
        header = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if header:
            current = Service(header.group(1), path)
            services.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    if not services:
        raise ValueError(
            f"{path}: parsed zero services. The line-based parser expects `services:` at column 0 "
            f"and two-space-indented service keys; if the file's shape changed, these guards would "
            f"otherwise pass by asserting over nothing."
        )
    return services


def check_kit_mounters_run_as_host_uid(failures: list[str]) -> None:
    """A dev service mounting the provisioned kit must run as the host uid."""
    for rel in DEV_COMPOSE_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: expected compose file is missing")
            continue
        for svc in parse_services(path):
            if not svc.writes_provisioned_kit():
                continue
            if svc.user is None:
                failures.append(
                    f"{svc.where}: mounts a written part of the provisioned NVFLARE kit but sets no `user:`. "
                    f"The kit is written by `make provision` as the host user, so a container "
                    f"running as the image's baked uid cannot write it and the entrypoint "
                    f"crash-loops on `local/`. Add: user: \"${{UID:-1000}}:1000\" (FLIP#1171)."
                )
            elif not svc.user.startswith("${UID"):
                failures.append(
                    f"{svc.where}: `user: {svc.user}` does not follow the host uid. "
                    f"Expected ${{UID:-1000}}:1000 so the container matches the owner of the "
                    f"provisioned kit (FLIP#1171)."
                )


def check_user_override_names_the_user(failures: list[str]) -> None:
    """Any service overriding `user:` must also set HOME/USER/LOGNAME.

    Exempt: `user: "0:0"`. Root has a real /etc/passwd entry, so getpwuid() resolves and
    HOME is supplied by the image — the Flower dev SuperLink relies on this.

    Scoped to the FL composes rather than every compose in the repo: the trust service
    images are still built with the developer's UNAME, so naming a fixed /home/flip for
    them would point at a directory their locally-built images do not have. That is a
    real latent hazard but a separate change from FLIP#1171.
    """
    for rel in DEV_COMPOSE_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for svc in parse_services(path):
            user = svc.user
            if user is None or user.startswith("0:"):
                continue
            missing = [k for k in IDENTITY_ENV_KEYS if k not in svc.env_keys()]
            if missing:
                failures.append(
                    f"{svc.where}: overrides `user:` but does not set {', '.join(missing)}. "
                    f"The overridden uid has no /etc/passwd entry, so pwd.getpwuid() raises: "
                    f"HOME falls back to '/' and getpass.getuser() fails partway through "
                    f"`import torch._dynamo`, surfacing as a torch mega-cache assertion "
                    f"nowhere near the cause (FLIP#1171)."
                )


def check_production_does_not_override_user(failures: list[str]) -> None:
    """Production runs as the image's baked user; nothing should re-point it."""
    for glob in PROD_COMPOSE_GLOBS:
        parent = REPO_ROOT / Path(glob).parent
        for path in sorted(parent.glob(Path(glob).name)):
            for svc in parse_services(path):
                if svc.user is not None:
                    failures.append(
                        f"{svc.where}: production compose sets `user: {svc.user}`. "
                        f"Prod runs as the image's baked non-root user (GHSA-8465) and EFS "
                        f"access points remap ownership regardless; the uid override is a "
                        f"dev-only accommodation for host-owned bind mounts (FLIP#1171)."
                    )


def check_no_uname_runtime_paths(failures: list[str]) -> None:
    """`/home/${UNAME}` as a runtime path is the build/runtime conflation itself.

    UNAME is a *build* arg. Using it to name a runtime path means the compose only works
    for images built by the same person who runs them, which is the whole of FLIP#1171.
    """
    for path in sorted((REPO_ROOT / "deploy").rglob("compose*.yml")) + sorted(
        (REPO_ROOT / "trust" / "deploy").rglob("compose*.yml")
    ):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if "/home/${UNAME}" in line:
                failures.append(
                    f"{path.relative_to(REPO_ROOT)}:{n}: uses /home/${{UNAME}} as a runtime path. "
                    f"UNAME is a build arg; naming a runtime path with it means the compose only "
                    f"works for images built by whoever runs them (FLIP#1171). Use /home/flip."
                )


def main() -> int:
    failures: list[str] = []
    try:
        check_kit_mounters_run_as_host_uid(failures)
        check_user_override_names_the_user(failures)
        check_production_does_not_override_user(failures)
        check_no_uname_runtime_paths(failures)
    except ValueError as exc:
        failures.append(str(exc))

    if failures:
        print("❌ container identity guards failed:\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("✅ container identity guards passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
