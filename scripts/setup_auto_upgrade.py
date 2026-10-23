#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
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
"""Install (or remove) automatic upgrades on a trust host: ``make setup-auto-upgrade KIT=<name>``.

A separate, interactive step on purpose: ``onboard_onprem_trust.py`` is a non-interactive gate that
runs on every ``up-onprem-trust``, and an ``input()`` there would hang scripted and CI installs.

One answer does the whole job:

1. creates the agent's own checkout (``/opt/flip/releases/FLIP``) so the operator's working tree is
   never moved under them;
2. moves the kit file to ``/etc/flip/trust/`` and leaves a symlink at its old path (the agent and the
   operator then share one file, so the ``DOCKER_TAG`` the upgrade writes is the one everyone reads);
3. installs a systemd service + hourly timer that runs ``trust_upgrade_agent.py tick``;
4. sets the hub toggle with the trust's own API key (``PUT /trust/upgrade-policy``).

Auto-upgrade can be switched off at any time from the Trust portal (My Trust), or here with
``AUTO_UPGRADE=no``, which stops the timer and tells the hub.

Usage:
    sudo -E uv run --no-config scripts/setup_auto_upgrade.py --kit-file trust/.env.<CODE>.<env> \\
        [--answer yes|no] [--repo-url URL] [--dry-run]

``--answer`` (``AUTO_UPGRADE=yes|no`` in the Makefile) skips the question for scripted installs.
Exit codes: 0 done (or declined), 2 a precondition failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ETC_KIT_DIR = Path("/etc/flip/trust")
CHECKOUT = Path("/opt/flip/releases/FLIP")
UNIT_DIR = Path("/etc/systemd/system")
UNIT_NAME = "flip-trust-upgrade"
DEFAULT_REPO_URL = "https://github.com/londonaicentre/FLIP.git"
HTTP_TIMEOUT_S = 15

DISCLOSURE = """\
Automatic upgrades keep this trust on the release the Central Hub runs.

  - Every hour a small agent on this host asks the hub whether a newer release is due.
  - It upgrades only when it is safe: no FL job is running on this trust, you are inside the
    maintenance window (if you set one), and this site has no local changes.
  - It runs the same data-safe upgrade you would run by hand, and it never retries a failed release.
  - You can switch this off at any time from the Trust portal (My Trust > Automatic upgrades),
    or by re-running this step with AUTO_UPGRADE=no.
"""


def service_unit(agent: Path, kit_file: Path, checkout: Path) -> str:
    """The oneshot the timer starts. ``uv run --no-config`` matches how the other scripts here are run."""
    return f"""[Unit]
Description=FLIP trust automatic upgrade check
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=/usr/bin/env uv run --no-config {agent} tick --kit-file {kit_file} --checkout {checkout}
# An upgrade can pull gigabytes and recreate every container; the hub's lease self-expires at 2h.
TimeoutStartSec=2h
"""


def timer_unit() -> str:
    return """[Unit]
Description=Hourly FLIP trust automatic upgrade check

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
"""


def run(cmd: list[str], dry_run: bool, **kw) -> subprocess.CompletedProcess | None:
    print("  $ " + " ".join(cmd))
    if dry_run:
        return None
    return subprocess.run(cmd, check=True, text=True, **kw)  # noqa: S603


def read_kit(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")
    return env


def set_hub_toggle(kit: dict[str, str], enabled: bool) -> None:
    """``PUT /trust/upgrade-policy`` with the trust's own key; raises on anything but success."""
    req = urllib.request.Request(  # noqa: S310 - https hub URL from the kit
        kit["CENTRAL_HUB_API_URL"].rstrip("/") + "/trust/upgrade-policy",
        data=json.dumps({"enabled": enabled}).encode(),
        headers={kit["TRUST_API_KEY_HEADER"]: kit["TRUST_API_KEY"], "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S):  # noqa: S310
        pass


def ask(question: str) -> bool:
    if not sys.stdin.isatty():
        raise SystemExit("not a terminal: pass --answer yes|no (AUTO_UPGRADE=yes|no) for a scripted install")
    return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")


def migrate_kit(kit_file: Path, dry_run: bool) -> Path:
    """Move the kit under /etc/flip/trust and symlink the old path. Idempotent; returns the new path."""
    if kit_file.is_symlink() and kit_file.resolve().parent == ETC_KIT_DIR:
        return kit_file.resolve()
    target = ETC_KIT_DIR / kit_file.name
    if target.exists():
        raise SystemExit(f"{target} already exists but {kit_file} is not its symlink; resolve by hand")
    print(f"moving the kit file to {target} (the old path becomes a symlink)")
    if not dry_run:
        ETC_KIT_DIR.mkdir(parents=True, exist_ok=True)
        ETC_KIT_DIR.chmod(0o750)
        shutil.move(str(kit_file), target)
        target.chmod(0o600)
        kit_file.symlink_to(target)
    return target


def ensure_checkout(repo_url: str, dry_run: bool) -> None:
    if (CHECKOUT / ".git").is_dir():
        print(f"agent checkout {CHECKOUT} exists")
        return
    print(f"creating the agent's own checkout at {CHECKOUT}")
    if not dry_run:
        CHECKOUT.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--no-checkout", repo_url, str(CHECKOUT)], dry_run)


def install(kit_file: Path, repo_url: str, dry_run: bool) -> None:
    kit = read_kit(kit_file)
    ensure_checkout(repo_url, dry_run)
    shared_kit = migrate_kit(kit_file, dry_run)
    agent = CHECKOUT / "scripts" / "trust_upgrade_agent.py"
    for name, text in (
        (f"{UNIT_NAME}.service", service_unit(agent, shared_kit, CHECKOUT)),
        (f"{UNIT_NAME}.timer", timer_unit()),
    ):
        print(f"writing {UNIT_DIR / name}")
        if not dry_run:
            (UNIT_DIR / name).write_text(text)
    run(["systemctl", "daemon-reload"], dry_run)
    run(["systemctl", "enable", "--now", f"{UNIT_NAME}.timer"], dry_run)
    if not dry_run:
        set_hub_toggle(kit, True)
    print("automatic upgrades are ON for this trust. Switch them off any time from My Trust in the portal.")


def uninstall(kit_file: Path, dry_run: bool) -> None:
    if (UNIT_DIR / f"{UNIT_NAME}.timer").exists():
        run(["systemctl", "disable", "--now", f"{UNIT_NAME}.timer"], dry_run)
    if not dry_run:
        set_hub_toggle(read_kit(kit_file), False)
    print("automatic upgrades are OFF. The agent checkout and the kit's new location are left in place.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kit-file", required=True, type=Path)
    p.add_argument("--answer", choices=("yes", "no"), help="skip the question (scripted installs)")
    p.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    p.add_argument("--dry-run", action="store_true", help="print what would change, change nothing")
    args = p.parse_args(argv)

    if not args.kit_file.is_file():
        print(f"kit file {args.kit_file} not found", file=sys.stderr)
        return 2
    if not args.dry_run and os.geteuid() != 0:
        print("run as root (sudo -E): this writes /etc/flip and a systemd unit", file=sys.stderr)
        return 2
    if shutil.which("systemctl") is None:
        print("systemd was not found on this host; automatic upgrades need it", file=sys.stderr)
        return 2
    if any(
        read_kit(args.kit_file).get(k, "").startswith(("ssh://", "tcp://")) for k in ("DOCKER_HOST", "DOCKER_CONTEXT")
    ):
        print("this kit drives a remote Docker host; automatic upgrades support on-prem hosts only", file=sys.stderr)
        return 2

    print(DISCLOSURE)
    yes = args.answer == "yes" if args.answer else ask("Turn on automatic upgrades for this trust?")
    if yes:
        install(args.kit_file, args.repo_url, args.dry_run)
    else:
        uninstall(args.kit_file, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
