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
"""The trust host's upgrade agent: one tick of "should this site move to the hub's release now?".

Run hourly by a systemd timer that ``make setup-auto-upgrade`` installs. It lives on the host, not in
a container, because the upgrade recreates every container, trust-api included, and a process that
is being replaced cannot be the one replacing it.

A tick asks the hub for this trust's policy (``GET /trust/upgrade-policy``, the trust's own API key),
then upgrades only when every condition holds: the switch is on, the site is behind a release, the
failed-release rule allows it, the clock is inside the maintenance window, no job holds the trust
and the checkout is clean. It then claims a lease (``POST /trust/upgrade-status`` ``STARTING``;
the hub re-checks "no job" under a row lock and refuses with 409 if one just landed) and runs the same
data-safe verb an operator would: ``make upgrade-onprem-trust``. The result is reported back and a
failure is never retried until an operator clears it in the portal.

``decide`` is pure and holds every rule; the rest of the file is I/O around it.

Usage:
    uv run --no-config trust_upgrade_agent.py tick --kit-file /etc/flip/trust/.env.<CODE>.<env> \\
        [--checkout /opt/flip/releases/FLIP] [--dry-run]

The checkout is the agent's own (default ``/opt/flip/releases/FLIP``): it is moved to each release tag, so
the operator's working tree is never touched, and the compose files and the upgrade verb always come from
the release being installed. The kit file lives outside both trees; ``KIT_FILE=`` points the Makefiles at it.

Exit codes: 0 the tick completed (including "nothing to do" and a refused claim), 1 the upgrade ran and
failed, 2 the hub or kit could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The agent's OWN checkout (plan decision 4): the operator's working tree is never moved under them.
DEFAULT_CHECKOUT = Path("/opt/flip/releases/FLIP")
# Kit file name token -> deploy/env_mode.mk PROD value.
ENV_TOKEN_TO_PROD = {"production": "true", "stag": "stag", "lza-prod": "lza", "lza-stag": "lza-stag"}
LOG_TAIL_BYTES = 6000
HTTP_TIMEOUT_S = 15
# Same shape the hub and flip-ui accept as a comparable release identity.
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")

# Why a tick did nothing; also what is reported for the blockers only this host can see.
LOCAL_MODIFICATIONS = "LOCAL_MODIFICATIONS"
UNSUPPORTED_MODE = "UNSUPPORTED_MODE"
UNKNOWN_CHECKOUT = "UNKNOWN_CHECKOUT"


@dataclass(frozen=True)
class Local:
    """What only this host can know."""

    current_release: str | None
    checkout_clean: bool | None  # None: git could not say
    unsupported: bool  # a remotely driven (EC2 / DOCKER_HOST) site


@dataclass(frozen=True)
class Decision:
    action: str  # "UPGRADE" | "SKIP" | "BLOCKED"
    reason: str
    release: str | None = None


def in_window(now_utc: datetime, window: dict | None, tz_name: str | None) -> bool:
    """Whether ``now_utc`` is inside the trust-local window; no window means always. End is exclusive."""
    if not window or not tz_name:
        return True
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
    here = local.hour * 60 + local.minute

    def minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    start, end = minutes(window["start"]), minutes(window["end"])
    return start <= here < end if start < end else here >= start or here < end


def decide(policy: dict, local: Local, now_utc: datetime) -> Decision:
    """Every rule for one tick, in the order an operator would check them."""
    if not policy.get("enabled"):
        return Decision("SKIP", "automatic upgrades are off")
    if policy.get("lease"):
        return Decision("SKIP", "an upgrade already holds the lease")
    target = policy.get("target_release")
    if not target or not RELEASE_TAG.match(target):
        return Decision("SKIP", "the hub runs no release tag to move to")
    if target == local.current_release:
        return Decision("SKIP", f"already on {target}")
    if policy.get("failed_release") == target:
        return Decision("SKIP", f"{target} failed earlier; an operator must clear it in the portal")
    if local.unsupported:
        return Decision("BLOCKED", UNSUPPORTED_MODE, target)
    if local.checkout_clean is None:
        return Decision("BLOCKED", UNKNOWN_CHECKOUT, target)
    if not local.checkout_clean:
        return Decision("BLOCKED", LOCAL_MODIFICATIONS, target)
    if not in_window(now_utc, policy.get("maintenance_window"), policy.get("timezone")):
        return Decision("SKIP", "outside the maintenance window")
    if policy.get("is_busy"):
        return Decision("SKIP", "a job holds this trust")
    return Decision("UPGRADE", f"{local.current_release or 'unknown'} -> {target}", target)


# --- I/O ---


def read_kit(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of the kit file; comments and blanks ignored, one level of quotes stripped."""
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def hub_call(kit: dict[str, str], method: str, route: str, body: dict | None = None) -> tuple[int, dict | None]:
    """Call the hub with the trust's own API key. Returns (status, json); 409 is a result, not an error."""
    url = kit["CENTRAL_HUB_API_URL"].rstrip("/") + route
    headers = {kit["TRUST_API_KEY_HEADER"]: kit["TRUST_API_KEY"], "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 - https hub URL from the kit
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return 409, None
        raise


def run(cmd: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, check=False, **kw)  # noqa: S603


def kit_identity(kit_file: Path) -> tuple[str, str]:
    """``(KIT, PROD)`` from ``.env.<KIT>.<env>``; the kit's name, not its FL slot, is what the Makefiles key on."""
    parts = kit_file.name.split(".", 3)  # ['', 'env', '<KIT>', '<env-token>']
    if len(parts) != 4 or parts[3] not in ENV_TOKEN_TO_PROD:
        raise ValueError(f"{kit_file.name} is not .env.<KIT>.<{'|'.join(ENV_TOKEN_TO_PROD)}>")
    return parts[2], ENV_TOKEN_TO_PROD[parts[3]]


def local_state(kit: dict[str, str], repo: Path) -> Local:
    """Current release from trust-api's own /health; clean checkout from git; remote-driven from the kit."""
    unsupported = any(kit.get(k, "").startswith(("ssh://", "tcp://")) for k in ("DOCKER_HOST", "DOCKER_CONTEXT"))
    status = run(["git", "status", "--porcelain", "--untracked-files=no"], repo)
    clean = (status.stdout.strip() == "") if status.returncode == 0 else None
    release = None
    try:
        port = kit.get("TRUST_API_PORT", "8020")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            version = json.loads(resp.read()).get("version")
            release = version if isinstance(version, str) else None
    except (urllib.error.URLError, OSError, ValueError):
        release = None
    return Local(release, clean, unsupported)


def checkout(repo: Path, tag: str) -> str | None:
    """Move the checkout to the release tag (compose files and the upgrade verb come from it); error text or None."""
    for cmd in (["git", "fetch", "--tags", "origin"], ["git", "checkout", "--detach", f"refs/tags/{tag}"]):
        out = run(cmd, repo)
        if out.returncode != 0:
            return (out.stderr or out.stdout)[-LOG_TAIL_BYTES:]
    return None


def upgrade(repo: Path, kit_file: Path, tag: str) -> tuple[bool, str]:
    """Move the agent's checkout to the tag, then run the operator's own verb. Returns (ok, log tail)."""
    err = checkout(repo, tag)
    if err:
        return False, f"git checkout {tag} failed:\n{err}"
    kit_name, prod = kit_identity(kit_file)
    out = run(
        [
            "make",
            "upgrade-onprem-trust",
            f"KIT={kit_name}",
            f"PROD={prod}",
            f"TAG={tag}",
            "YES=1",
            f"KIT_FILE={kit_file}",
        ],
        repo,
    )
    return out.returncode == 0, (out.stdout + out.stderr)[-LOG_TAIL_BYTES:]


def tick(kit_file: Path, repo: Path, dry_run: bool) -> int:
    try:
        kit = read_kit(kit_file)
        _, policy = hub_call(kit, "GET", "/trust/upgrade-policy")
    except (OSError, KeyError, urllib.error.URLError) as exc:
        print(f"cannot read the policy: {exc}", file=sys.stderr)
        return 2
    assert policy is not None
    decision = decide(policy, local_state(kit, repo), datetime.now(timezone.utc).replace(tzinfo=None))
    print(f"{decision.action}: {decision.reason}")
    if decision.action == "SKIP" or dry_run:
        return 0
    assert decision.release is not None
    if decision.action == "BLOCKED":
        hub_call(
            kit,
            "POST",
            "/trust/upgrade-status",
            {"state": "BLOCKED", "release": decision.release, "detail": decision.reason},
        )
        return 0
    status, _ = hub_call(kit, "POST", "/trust/upgrade-status", {"state": "STARTING", "release": decision.release})
    if status == 409:
        print("the hub refused the claim (a job landed, or the policy changed); nothing done")
        return 0
    ok, tail = upgrade(repo, kit_file, decision.release)
    report = {"state": "SUCCEEDED" if ok else "FAILED", "release": decision.release}
    if not ok:
        report["detail"] = tail
    hub_call(kit, "POST", "/trust/upgrade-status", report)
    print("SUCCEEDED" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick", help="decide, and if due, upgrade")
    t.add_argument("--kit-file", required=True, type=Path)
    t.add_argument("--checkout", type=Path, default=DEFAULT_CHECKOUT, help="the agent's own FLIP checkout")
    t.add_argument("--dry-run", action="store_true", help="print the decision, change nothing")
    args = parser.parse_args(argv)
    return tick(args.kit_file, args.checkout, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
