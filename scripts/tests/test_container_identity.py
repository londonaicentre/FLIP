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

- A dev service that mounts the host-provisioned FLARE kit must run as the host uid,
  or the entrypoint's ``envsubst`` into the bind-mounted ``local/`` fails and the
  container crash-loops. This was missed once already, in the standalone harness
  (``fl-services/nvflare/compose.dev.yml``), which the deploy composes' fix did not
  reach.
- A service that overrides ``user:`` must also name that user, because the overridden
  uid is absent from the image's ``/etc/passwd``: ``pwd.getpwuid()`` raises, ``HOME``
  falls back to ``/``, and the symptom surfaces three layers away as torch's
  "Artifact of type=precompile already registered in mega-cache artifact factory".
- Prod composes must not override ``user:`` at all: prod runs as the image's
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
# FLARE stages job payloads through `transfer/`; `startup/` holds the certs and is
# only read. fl-api in the deploy composes mounts just `admin/local` and
# `admin/startup`, writes neither, and so legitimately runs as the image's own uid —
# whereas the standalone harness also gives it `admin/transfer`, which it does write.
KIT_WRITE_TARGETS = ("/app/local", "/app/transfer", "/app/admin/transfer")

DEV_COMPOSE_FILES = (
    "deploy/compose.development.nvflare.yml",
    "deploy/compose.development.flower.yml",
    "trust/deploy/compose/compose_trust.development.nvflare.yml",
    "fl-services/nvflare/compose.dev.yml",
)

# Dev services that must not run as root, named rather than inferred: each reads
# host-provisioned material and shares host-owned bind mounts with flip-api, so a root
# process in them rewrites the operator's own files as root (FLIP#1320).
NON_ROOT_DEV_SERVICES = {
    "deploy/compose.development.flower.yml": ("fl-server-net-1", "fl-server-net-2"),
}

PROD_COMPOSE_GLOBS = (
    "deploy/compose.prod*.yml",
    "trust/deploy/compose/compose_trust.prod*.yml",
)

IDENTITY_ENV_KEYS = ("HOME", "USER", "LOGNAME")

# Paths the SuperLink and the ServerApp it spawns actually write: the Flower cache under
# HOME, and the app templates' output dir under WORKING_DIR (default `/app/runs`). Both
# default into /app, which the image owns as app:app 0755, so a host-uid container has to
# re-point them or the run dies after its last round (FLIP#1320).
WRITABLE_PATH_ENV_KEYS = ("HOME", "WORKING_DIR")


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

    def group_add_entries(self) -> list[str]:
        """The inline `group_add: ["a", "b"]` entries, or [] when the key is absent.

        Only the inline-list form is parsed: that is what every compose file here uses,
        and a block list would need the real YAML load this module deliberately avoids.
        """
        for line in self.lines:
            m = re.match(r"\s{4}group_add:\s*\[(.*)\]\s*$", line)
            if m:
                return [e.strip().strip("\"'") for e in m.group(1).split(",") if e.strip()]
        return []

    def env_keys(self) -> set[str]:
        return set(self.env_values())

    def env_values(self) -> dict[str, str]:
        """The service's inline `- KEY=value` environment entries."""
        values: dict[str, str] = {}
        for line in self.lines:
            m = re.match(r"\s{6}-\s*([A-Za-z_][A-Za-z0-9_]*)=(.*?)\s*$", line)
            if m:
                values[m.group(1)] = m.group(2).strip("\"'")
        return values


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
                    f"{svc.where}: mounts a written part of the provisioned FLARE kit but sets no `user:`. "
                    f"The kit is written by `make provision` as the host user, so a container "
                    f"running as the image's baked uid cannot write it and the entrypoint "
                    f'crash-loops on `local/`. Add: user: "${{UID:-1000}}:1000" (FLIP#1171).'
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
    HOME is supplied by the image. No dev service should be taking that exemption now
    (check_dev_fl_services_are_non_root), but the carve-out stays so this guard reports
    the missing identity env rather than silently passing a root service.

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


def check_dev_fl_services_are_non_root(failures: list[str]) -> None:
    """Named dev FL services must run as the host user, with the host gid available.

    The Flower dev SuperLink ran as root so it could read the provisioned 0640 TLS keys
    (and, before FLIP#1291, refresh the operator's AWS SSO token). Root reads those, but
    it also rewrites every host-owned file it touches as root:root: the SSO token it
    refreshed became unreadable to the operator's own `aws` CLI, and the bundle dir it
    shares with flip-api is open to the same thing. The host uid reads the keys as their
    owner; `group_add` with the host gid is the second route, for a kit provisioned under
    a different uid from the one running the stack (FLIP#1320).

    The same services must also point HOME and WORKING_DIR outside /app. Both default
    into the image tree, which is app:app 0755: left alone, the SuperLink cannot write
    its ~/.flwr cache and the ServerApp (a subprocess of the SuperLink, so the same uid)
    finishes every round and then dies at `output_dir.mkdir()` with PermissionError,
    having uploaded nothing while the hub model still reads RUNNING.
    """
    for rel, names in NON_ROOT_DEV_SERVICES.items():
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: expected compose file is missing")
            continue
        services = {svc.name: svc for svc in parse_services(path)}
        for name in names:
            svc = services.get(name)
            if svc is None:
                failures.append(
                    f"{rel}:{name}: expected service is missing, so its non-root guard "
                    f"asserts over nothing. Update NON_ROOT_DEV_SERVICES if it was renamed."
                )
                continue
            if svc.user is None or svc.user.startswith("0:"):
                failures.append(
                    f"{svc.where}: runs as root (`user: {svc.user}`). A root container "
                    f"rewrites host-owned bind mounts as root:root and locks the operator "
                    f"out of their own files. Use the host uid with the image gid: "
                    f'user: "${{UID:-1000}}:49999" (FLIP#1320).'
                )
            elif not svc.user.startswith("${UID"):
                failures.append(
                    f"{svc.where}: `user: {svc.user}` does not follow the host uid, so it "
                    f"cannot read the host-provisioned 0640 TLS keys as their owner "
                    f"(FLIP#1320)."
                )
            if not svc.group_add_entries():
                failures.append(
                    f"{svc.where}: no `group_add:`. The provisioned TLS keys are 0640 and "
                    f"owner plus group only, so the host gid is the second route to them "
                    f"when the kit was provisioned under another uid (FLIP#1320)."
                )
            elif not any("DOCKER_GID" in entry for entry in svc.group_add_entries()):
                failures.append(
                    f"{svc.where}: `group_add: {svc.group_add_entries()}` does not use "
                    f"${{DOCKER_GID}}. A hard-coded gid is either the wrong group for the "
                    f"provisioned keys or someone else's group (FLIP#1320)."
                )
            env = svc.env_values()
            for key in WRITABLE_PATH_ENV_KEYS:
                value = env.get(key)
                if value is None:
                    failures.append(
                        f"{svc.where}: does not set {key}. The image writes it under /app, "
                        f"which is app:app 0755, so the host uid cannot create it: the "
                        f"SuperLink's caches and the ServerApp's output dir fail far from "
                        f"the cause. Point it at /tmp (FLIP#1320)."
                    )
                elif value == "/app" or value.startswith("/app/"):
                    failures.append(
                        f"{svc.where}: `{key}={value}` is under /app, which the image owns as "
                        f"app:app 0755. The host uid cannot write there, so setting the "
                        f"variable is not enough: it has to point somewhere this uid can "
                        f"create, such as /tmp (FLIP#1320)."
                    )


def check_production_does_not_override_user(failures: list[str]) -> None:
    """Production runs as the image's baked user; nothing should re-point it."""
    for glob in PROD_COMPOSE_GLOBS:
        parent = REPO_ROOT / Path(glob).parent
        for path in sorted(parent.glob(Path(glob).name)):
            for svc in parse_services(path):
                if svc.user is not None:
                    failures.append(
                        f"{svc.where}: prod compose sets `user: {svc.user}`. "
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
        check_dev_fl_services_are_non_root(failures)
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
