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
data-safe verb an operator would: ``make upgrade-onprem-trust``. A failed attempt is first put back on
the release the site came from — once, inside the same lease — and the report says whether that took.
The result is then reported back, and a failure is never retried until an operator clears it in the
portal.

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
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The agent's OWN checkout: the operator's working tree is never moved under them.
DEFAULT_CHECKOUT = Path("/opt/flip/releases/FLIP")
# Kit file name token -> deploy/env_mode.mk HUB value (FLIP#1479 renamed the selector and the
# kit tokens; the pre-rename names are still accepted for one release, as the Makefiles do).
ENV_TOKEN_TO_HUB = {"prod": "prod", "stag": "stag", "production": "prod", "lza-prod": "prod", "lza-stag": "stag"}
LOG_TAIL_BYTES = 6000
HTTP_TIMEOUT_S = 15
# After the upgrade verb returns, how long the site has to come up ON the target release before the
# attempt is called FAILED. Generous because XNAT's in-place schema upgrade dominates it and a slow
# one must not park the site: FAILED stops this release being retried until an operator clears it.
VERIFY_TIMEOUT_S = 600
# A terminal report is retried: the hub only learns the outcome here, and a lost report
# holds the lease for its full TTL and then re-runs the same release.
REPORT_ATTEMPTS = 4
REPORT_RETRY_WAIT_S = 5
VERIFY_POLL_S = 10
# A rollback is judged on a shorter leash than a forward upgrade: the previous release is known to
# have worked on this site, so a slow return is itself the finding rather than something to wait out.
ROLLBACK_VERIFY_TIMEOUT_S = 300
# While `make` runs, re-post STARTING this often. The hub already treats a same-release STARTING as a
# renewal, so this needs no new protocol: it means the 2h TTL only ever expires on a dead agent,
# never on a slow-but-working upgrade.
LEASE_RENEW_EVERY_S = 20 * 60
# Same shape the hub and flip-ui accept as a comparable release identity.
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")

# Why a tick did nothing; also what is reported for the blockers only this host can see.
LOCAL_MODIFICATIONS = "LOCAL_MODIFICATIONS"
UNSUPPORTED_MODE = "UNSUPPORTED_MODE"
UNKNOWN_CHECKOUT = "UNKNOWN_CHECKOUT"
SITE_UNHEALTHY = "SITE_UNHEALTHY"


@dataclass(frozen=True)
class Health:
    """trust-api's own ``/health``, as far as this host could read it."""

    reachable: bool
    status: str | None  # "ok" | "degraded"
    version: str | None
    hub_key_match: bool | None
    dead_tasks: tuple[str, ...]

    @property
    def fit_to_upgrade(self) -> str | None:
        """Why this site must not be touched, or None when it is healthy enough to upgrade."""
        if not self.reachable:
            return "trust-api /health is unreachable"
        if self.status != "ok":
            return f"trust-api reports status={self.status}" + (
                f" (dead background tasks: {', '.join(self.dead_tasks)})" if self.dead_tasks else ""
            )
        if self.dead_tasks:
            return f"dead background tasks: {', '.join(self.dead_tasks)}"
        if self.hub_key_match is not True:
            return (
                "this trust's AES key does not match the hub's (hub_key_match="
                f"{self.hub_key_match}); the kit needs refreshing before an upgrade"
            )
        return None


@dataclass(frozen=True)
class Local:
    """What only this host can know."""

    current_release: str | None
    checkout_clean: bool | None  # None: git could not say
    unsupported: bool  # a remotely driven (EC2 / DOCKER_HOST) site
    health: Health  # always read: a skippable pre-flight is a representable "health check never ran"


@dataclass(frozen=True)
class Decision:
    action: str  # "UPGRADE" | "SKIP" | "BLOCKED"
    reason: str
    release: str | None = None
    detail: str | None = None  # human-readable "why", reported with a BLOCKED code


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
    # Pre-flight: never touch a site that is already broken. An upgrade recreates every container,
    # so a site that cannot be verified healthy first cannot be told apart afterwards from one this
    # agent broke — and a mismatched AES key would survive the recreate as a dead trust.
    unfit = local.health.fit_to_upgrade
    if unfit:
        return Decision("BLOCKED", SITE_UNHEALTHY, target, unfit)
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
    """``(KIT, HUB)`` from ``.env.<KIT>.<env>``; the kit's name, not its FL slot, is what the Makefiles key on."""
    parts = kit_file.name.split(".", 3)  # ['', 'env', '<KIT>', '<env-token>']
    if len(parts) != 4 or parts[3] not in ENV_TOKEN_TO_HUB:
        raise ValueError(f"{kit_file.name} is not .env.<KIT>.<{'|'.join(ENV_TOKEN_TO_HUB)}>")
    return parts[2], ENV_TOKEN_TO_HUB[parts[3]]


def read_health(kit: dict[str, str]) -> Health:
    """trust-api's own ``/health``, read over loopback. Never raises: unreachable is a Health too."""
    port = kit.get("TRUST_API_PORT", "8020")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return Health(False, None, None, None, ())
    if not isinstance(body, dict):
        return Health(False, None, None, None, ())
    version = body.get("version")
    status = body.get("status")
    dead = body.get("dead_tasks")
    return Health(
        reachable=True,
        status=status if isinstance(status, str) else None,
        version=version if isinstance(version, str) else None,
        hub_key_match=body.get("hub_key_match") if isinstance(body.get("hub_key_match"), bool) else None,
        dead_tasks=tuple(str(t) for t in dead) if isinstance(dead, list) else (),
    )


def local_state(kit: dict[str, str], repo: Path) -> Local:
    """Current release and fitness from trust-api's /health; clean checkout from git; remote-driven from the kit."""
    unsupported = any(kit.get(k, "").startswith(("ssh://", "tcp://")) for k in ("DOCKER_HOST", "DOCKER_CONTEXT"))
    status = run(["git", "status", "--porcelain", "--untracked-files=no"], repo)
    clean = (status.stdout.strip() == "") if status.returncode == 0 else None
    health = read_health(kit)
    return Local(health.version, clean, unsupported, health)


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
    kit_name, hub = kit_identity(kit_file)
    out = run(
        [
            "make",
            "upgrade-onprem-trust",
            f"KIT={kit_name}",
            f"HUB={hub}",
            f"TAG={tag}",
            "YES=1",
            f"KIT_FILE={kit_file}",
        ],
        repo,
    )
    return out.returncode == 0, (out.stdout + out.stderr)[-LOG_TAIL_BYTES:]


def verify(
    kit: dict[str, str],
    tag: str,
    *,
    timeout_s: int = VERIFY_TIMEOUT_S,
    poll_s: int = VERIFY_POLL_S,
    sleep=time.sleep,
    now=time.monotonic,
) -> tuple[bool, str]:
    """Poll /health until the site is up ON ``tag``. Returns (verified, what the last read said)."""
    deadline = now() + timeout_s
    health = read_health(kit)
    while True:
        if health.reachable and health.status == "ok" and not health.dead_tasks and health.version == tag:
            return True, f"trust-api reports {tag}, status=ok"
        if now() >= deadline:
            break
        sleep(poll_s)
        health = read_health(kit)
    if not health.reachable:
        last = "trust-api /health never came back"
    elif health.version != tag:
        last = f"trust-api reports {health.version or 'no version'}, not {tag}"
    else:
        last = f"trust-api reports status={health.status}" + (
            f", dead background tasks: {', '.join(health.dead_tasks)}" if health.dead_tasks else ""
        )
    return False, f"the upgrade applied but the site did not come up on {tag} within {timeout_s}s: {last}"


def rollback(
    kit: dict[str, str],
    run_upgrade: Callable[[str], tuple[bool, str]],
    previous: str | None,
    failed: str,
) -> tuple[bool, str]:
    """Put the site back on the release it came from, once, after a failed attempt.

    A failed upgrade leaves the site half-applied — some containers on the new release, some on the
    old, or a release Helm marked failed. Moving again is the only way back to something known to
    work there, and waiting for a human means waiting with the site in that state.

    Deliberately one attempt and no loop: a rollback that does not take is reported, not retried.
    The distinction the operator needs is "failed, site is back on vX" versus "failed, site is still
    on the release that broke it", so the outcome of this is part of the failure report either way.
    """
    if not previous:
        return False, "no previous release recorded, so nothing to roll back to"
    if not RELEASE_TAG.match(previous):
        return False, f"the previous release ({previous!r}) is not a tag, so it cannot be rolled back to"
    if previous == failed:
        return False, f"the site was already on {failed} before this attempt, so there is nothing to roll back to"

    ok, tail = run_upgrade(previous)
    if not ok:
        return False, f"automatic rollback to {previous} failed:\n{tail}"

    verified, what = verify(kit, previous, timeout_s=ROLLBACK_VERIFY_TIMEOUT_S)
    if not verified:
        return False, f"automatic rollback to {previous} applied but the site did not come up on it: {what}"
    return True, f"rolled back to {previous}, which came up"


def report_status(kit: dict[str, str], body: dict, attempts: int = REPORT_ATTEMPTS) -> bool:
    """POST a terminal report, retrying: losing it holds the lease and loses the outcome."""
    for attempt in range(1, attempts + 1):
        try:
            hub_call(kit, "POST", "/trust/upgrade-status", body)
        except (OSError, urllib.error.URLError) as exc:
            print(f"reporting {body['state']} failed (attempt {attempt}/{attempts}): {exc}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(REPORT_RETRY_WAIT_S * attempt)
        else:
            return True
    return False


class LeaseRenewal:
    """Re-posts STARTING while `make` runs, so an upgrade slower than the lease cannot lose it.

    The hub treats a STARTING for the release already holding the lease as a renewal (it resets the
    expiry rather than refusing), so this needs no new endpoint. A failed renewal is ignored on
    purpose: the upgrade is the fact, the lease is bookkeeping, and the final report is what the hub
    acts on — an exception here would abort an upgrade that is going perfectly well.

    Stops before the terminal report is posted, so a late renewal cannot re-claim a release the
    report has just marked FAILED.
    """

    def __init__(self, kit: dict[str, str], release: str, every: float = LEASE_RENEW_EVERY_S) -> None:
        self._kit = kit
        self._release = release
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LeaseRenewal":
        self._thread = threading.Thread(target=self._run, name="lease-renewal", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=HTTP_TIMEOUT_S * 2)

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            try:
                hub_call(self._kit, "POST", "/trust/upgrade-status", {"state": "STARTING", "release": self._release})
            except Exception as exc:  # noqa: BLE001 - a renewal is never worth failing the upgrade for
                print(f"lease renewal failed ({type(exc).__name__}: {exc}); continuing", file=sys.stderr)


def tick(kit_file: Path, repo: Path, dry_run: bool) -> int:
    try:
        kit = read_kit(kit_file)
        # Before anything is claimed: a kit this agent cannot name would otherwise fail inside
        # `upgrade()` with the lease already held, and repeat on every lease expiry.
        kit_identity(kit_file)
        _, policy = hub_call(kit, "GET", "/trust/upgrade-policy")
    except (OSError, KeyError, ValueError, urllib.error.URLError) as exc:
        print(f"cannot read the policy: {exc}", file=sys.stderr)
        return 2
    assert policy is not None
    state = local_state(kit, repo)
    decision = decide(policy, state, datetime.now(timezone.utc).replace(tzinfo=None))
    print(f"{decision.action}: {decision.reason}")
    if decision.action == "SKIP" or dry_run:
        return 0
    assert decision.release is not None
    if decision.action == "BLOCKED":
        report_status(
            kit,
            {
                "state": "BLOCKED",
                "release": decision.release,
                "blocker": decision.reason,
                "detail": decision.detail or decision.reason,
            },
        )
        return 0
    status, _ = hub_call(kit, "POST", "/trust/upgrade-status", {"state": "STARTING", "release": decision.release})
    if status == 409:
        print("the hub refused the claim (a job landed, or the policy changed); nothing done")
        return 0
    # From here the lease is held: every exit must tell the hub, or the release is silently
    # re-attempted after the lease expires.
    ok = False
    detail = "the agent stopped before it could report an outcome"
    try:
        # Renewal covers the verification too: it is a ten-minute wait on the site coming back.
        with LeaseRenewal(kit, decision.release):
            run_upgrade = lambda tag: upgrade(repo, kit_file, tag)  # noqa: E731 - one line, one backend
            ok, tail = run_upgrade(decision.release)
            detail = tail
            if ok:
                # The verb exiting 0 only says the deployment was applied. Confirm the site actually
                # came back up ON the target before telling the hub it SUCCEEDED.
                ok, detail = verify(kit, decision.release)
                print(detail)
            if not ok:
                # Either the verb failed or the site did not come back on the target. Both leave it
                # half-applied, so put it back on the release it was on — inside the same lease, so
                # no second agent can start while this is happening.
                rolled, how = rollback(kit, run_upgrade, state.current_release, decision.release)
                detail = f"{detail}\n{how}"
                print(how)
    except BaseException as exc:  # noqa: BLE001 - including SystemExit/KeyboardInterrupt: the report matters more
        ok, detail = False, f"the upgrade raised {type(exc).__name__}: {exc}"
        print(detail, file=sys.stderr)
        raise
    finally:
        report = {"state": "SUCCEEDED" if ok else "FAILED", "release": decision.release}
        if not ok:
            report["detail"] = detail
        report_status(kit, report)
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
