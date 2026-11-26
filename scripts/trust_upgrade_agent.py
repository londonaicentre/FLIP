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
"""The Kubernetes trust's upgrade agent: one tick of "should this site move to the hub's release now?".

Runs as a CronJob the flip-trust chart owns (``autoUpgrade.enabled``), in the trust's own namespace.
Automatic upgrades are for Kubernetes trusts only: a Docker Compose trust is upgraded by its operator
with ``make upgrade-onprem-trust``.

A tick asks the hub for this trust's policy (``GET /trust/upgrade-policy``, the trust's own API key),
then upgrades only when every condition holds: the switch is on, the site is behind a release, the
failed-release rule allows it, the site is healthy, the clock is inside the maintenance window and no
job holds the trust. It then claims a lease (``POST /trust/upgrade-status`` ``STARTING``; the hub
re-checks "no job" under a row lock and refuses with 409 if one just landed), pulls the published chart
for the target release and runs ``helm upgrade --reset-then-reuse-values`` (the live release already
holds the per-trust values, so the pod needs no kit parsing and no generated override). It waits for
the site to come up on the target, checks the governance document was not changed outside Helm, and
reports the outcome. A failed attempt is never rolled back by the agent and never retried until an
operator clears it in the portal.

``decide`` is pure and holds every rule; the rest of the file is I/O around it.

Usage (the chart's CronJob passes these):
    trust_upgrade_agent.py tick --namespace <ns> --release <name> \\
        --chart-repository oci://ghcr.io/londonaicentre/charts/flip-trust --kit-name <CODE> --hub prod \\
        --budget-seconds <n> [--dry-run]

Exit codes: 0 the tick completed (including "nothing to do" and a refused claim), 1 the upgrade ran and
failed, 2 the hub or the projected key could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The projection carrying ONLY the trust's API key (a Secret volume `items:` list, so
# the kubelet mounts it without the pod's ServiceAccount holding any `secrets` verb).
CLUSTER_KIT_DIR = Path("/etc/flip/upgrade-kit")
DEFAULT_TRUST_API_KEY_HEADER = "X-Trust-API-Key"  # pragma: allowlist secret
# Helm keeps release records as Secrets by default, and its lookup LISTS them by label — which RBAC
# cannot narrow to a name, and which returns their contents. ConfigMaps put the agent's writes in a
# smaller blast radius; the deploy side has to agree, or the record is invisible to it.
HELM_DRIVER = "configmap"
LOG_TAIL_BYTES = 6000
HTTP_TIMEOUT_S = 15
# After helm upgrade returns, how long the site has to come up ON the target release before the
# attempt is called FAILED. Generous because XNAT's in-place schema upgrade dominates it and a slow
# one must not park the site: FAILED stops this release being retried until an operator clears it.
VERIFY_TIMEOUT_S = 600
# A terminal report is retried: the hub only learns the outcome here, and a lost report
# holds the lease for its full TTL and then re-runs the same release.
REPORT_ATTEMPTS = 4
REPORT_RETRY_WAIT_S = 5
VERIFY_POLL_S = 10
# The Job's activeDeadlineSeconds kills the pod, and a killed pod does not report, so the
# hub would hold the lease for its full TTL and retry the same release without latching the failure.
# The chart therefore passes --budget-seconds (the deadline minus a margin), and every helm call and
# health wait gets only what is left of it, less this reserve for the terminal report
# (REPORT_ATTEMPTS x HTTP_TIMEOUT_S plus the retry waits is about 90 s).
REPORT_RESERVE_S = 120
_deadline: float | None = None


def set_budget(seconds: int | None, now: Callable[[], float] = time.monotonic) -> None:
    """Start the agent's own clock; None means no budget (the chart always passes one)."""
    global _deadline
    _deadline = None if seconds is None else now() + seconds


def remaining_budget(now: Callable[[], float] = time.monotonic) -> float | None:
    """Seconds left for work before the report reserve, or None when there is no budget."""
    if _deadline is None:
        return None
    return max(0.0, _deadline - now() - REPORT_RESERVE_S)


def _terminated(signum: int, _frame: object) -> None:
    """SIGTERM (the kubelet stopping the pod) becomes SystemExit, so tick's `finally` still reports."""
    raise SystemExit(f"stopped by signal {signum} before the upgrade finished")


# While the upgrade runs, re-post STARTING this often. The hub already treats a same-release STARTING as a
# renewal, so this needs no new protocol: it means the 2h TTL only ever expires on a dead agent,
# never on a slow-but-working upgrade.
LEASE_RENEW_EVERY_S = 20 * 60
# Same shape the hub and flip-ui accept as a comparable release identity.
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+([-.][0-9A-Za-z.-]+)?$")
# What an unattended upgrade may move TO: a stable release only, whatever the hub offers.
STABLE_RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(.*)$")


def release_key(tag: str | None) -> tuple[int, int, int, int] | None:
    """Sortable key for a ``vX.Y.Z[suffix]`` tag; a pre-release sorts below its release. None if unparseable."""
    m = _VERSION.match(tag or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if m.group(4) else 1


# The one blocker only the agent can see, reported to the hub with what failed.
SITE_UNHEALTHY = "SITE_UNHEALTHY"


@dataclass(frozen=True)
class Health:
    """trust-api's own ``/health``, as far as the agent could read it."""

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
    """What only the agent can read: the release Helm records, and the site's health."""

    current_release: str | None
    health: Health  # always read: a skippable pre-flight is a representable "health check never ran"


@dataclass(frozen=True)
class Cluster:
    """Where the agent runs: the trust's namespace, the Helm release it moves, and where to pull charts."""

    namespace: str
    release: str
    chart_repository: str
    kit_name: str
    hub: str
    kit_dir: Path = CLUSTER_KIT_DIR


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
    if not STABLE_RELEASE.match(target):
        return Decision("SKIP", f"{target} is not a stable release; only vX.Y.Z is applied unattended")
    # Helm's record says what was applied, not what came up: after a failed attempt the release is
    # recorded on the target while trust-api still serves the old build. A trust-api that answers
    # with another build is therefore not "already on" it, and the failed_release rule below (or,
    # once an operator clears it, a retry) decides. One that cannot be read at all proves nothing
    # either way, and a trust with nothing to do does not report a blocker.
    if target == local.current_release and local.health.version in (None, target):
        return Decision("SKIP", f"already on {target}")
    # Never follow the hub down: a hub rollback must not walk every opted-in site back across
    # migrations it has already run. Moving down is an operator's decision.
    here, there = release_key(local.current_release), release_key(target)
    if here is not None and there is not None and there < here:
        return Decision("SKIP", f"{target} is older than {local.current_release}; not moving a site down")
    if policy.get("failed_release") == target:
        return Decision("SKIP", f"{target} failed earlier; an operator must clear it in the portal")
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
    """Run ``cmd``; under a budget, stop it when the budget runs out and return exit 124."""
    budget = remaining_budget()
    if budget is not None:
        kw.setdefault("timeout", budget)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, check=False, **kw)  # noqa: S603
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]} stopped: the agent's time budget ran out")


def trust_api_base(kit: dict[str, str]) -> str:
    """Where trust-api answers: the in-cluster Service URL the chart hands the pod (``cluster_kit`` requires it)."""
    return kit["TRUST_API_URL"].rstrip("/")


def read_health(kit: dict[str, str]) -> Health:
    """trust-api's own ``/health``. Never raises: unreachable is a Health too."""
    try:
        with urllib.request.urlopen(f"{trust_api_base(kit)}/health", timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
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


def cluster_kit(cluster: Cluster) -> dict[str, str]:
    """The kit a pod is given: the API key projected as a file, the non-secret half from the env.

    Deliberately not a kit file. The projection carries one key and nothing else, so the pod never
    holds the trust's OMOP, XNAT or Grafana passwords, and the identity it runs under needs no
    ``secrets`` verb to read them.
    """
    try:
        key = (cluster.kit_dir / "trust-api-key").read_text().strip()
    except OSError as exc:
        raise ValueError(f"cannot read the projected API key at {cluster.kit_dir}/trust-api-key: {exc}") from exc
    kit = {
        "CENTRAL_HUB_API_URL": os.environ.get("CENTRAL_HUB_API_URL", ""),
        "TRUST_API_KEY_HEADER": os.environ.get("TRUST_API_KEY_HEADER") or DEFAULT_TRUST_API_KEY_HEADER,
        "TRUST_API_KEY": key,
        "TRUST_API_URL": os.environ.get("TRUST_API_URL", ""),
    }
    if not kit["CENTRAL_HUB_API_URL"]:
        raise ValueError("CENTRAL_HUB_API_URL is empty; the CronJob takes it from trustApi.env")
    # Without it read_health would fall back to the pod's own loopback, where nothing listens, and
    # every tick would report the healthy site as SITE_UNHEALTHY.
    if not kit["TRUST_API_URL"]:
        raise ValueError("TRUST_API_URL is empty; the CronJob sets it to the trust-api Service")
    return kit


def cluster_state(cluster: Cluster) -> Local:
    """Current release from the Helm record, health from trust-api.

    ``app_version`` is the release tag (the chart's ``version`` is SemVer and cannot carry the ``v``,
    so the release tag rides on ``appVersion``). Two sources can disagree for a moment after an
    upgrade: Helm's record is the fact the next upgrade acts on, so it wins, and ``/health`` is only
    the fallback when the record cannot be read.
    """
    health = read_health(cluster_kit(cluster))
    out = run(
        ["helm", "list", "--namespace", cluster.namespace, "--filter", f"^{cluster.release}$", "-o", "json"],
        Path.cwd(),
        env={**os.environ, "HELM_DRIVER": HELM_DRIVER},
    )
    current = None
    if out.returncode == 0:
        try:
            releases = json.loads(out.stdout or "[]")
        except json.JSONDecodeError:
            releases = []
        if releases:
            current = releases[0].get("app_version") or None
    return Local(current or health.version, health)


def cluster_upgrade(cluster: Cluster, tag: str) -> tuple[bool, str]:
    """Pull the published chart for ``tag`` and move the release onto it, keeping the live values.

    ``--reset-then-reuse-values`` keeps the per-trust values: the release already holds the values
    ``sync-kit`` last wrote, so the pod parses no kit and generates no override file. Not
    plain ``--reuse-values``: that renders against the *previous* chart's defaults, so a value the new
    release adds would be missing at render time. This flag takes the new chart's defaults and re-applies
    the stored overrides on top (Helm 3.14+; the agent image pins 3.17.3). Only
    ``global.image.tag`` moves — the per-image pins (omopDb, orthanc, xnat, flClient) are deliberate
    settings the kit owns, and an upgrade must not quietly reset them.

    No ``--install``: a release the configmap driver cannot see (one still recorded as Secrets) has
    to fail loudly here rather than be installed a second time underneath the running one.
    """
    env = {**os.environ, "HELM_DRIVER": HELM_DRIVER}
    version = tag[1:] if tag.startswith("v") else tag
    with tempfile.TemporaryDirectory() as tmp:
        pulled = run(
            ["helm", "pull", cluster.chart_repository, "--version", version, "--untar", "--untardir", tmp],
            Path(tmp),
            env=env,
        )
        if pulled.returncode != 0:
            tail = (pulled.stderr or pulled.stdout)[-LOG_TAIL_BYTES:]
            return False, f"helm pull {cluster.chart_repository}:{version} failed:\n{tail}"
        upgraded = run(
            [
                "helm",
                "upgrade",
                cluster.release,
                str(Path(tmp) / "flip-trust"),
                "--namespace",
                cluster.namespace,
                "--reset-then-reuse-values",
                "--set",
                f"global.image.tag={tag}",
                "--history-max",
                "10",
            ],
            Path(tmp),
            env=env,
        )
    return upgraded.returncode == 0, (upgraded.stdout + upgraded.stderr)[-LOG_TAIL_BYTES:]


GOVERNANCE_COMPONENT = "governance"
GOVERNANCE_KEY = "governance.toml"


def _document(text: str) -> str:
    """A governance document as content: no sheet indentation, no trailing space, no blank edges."""
    return "\n".join(line.rstrip() for line in textwrap.dedent(text).strip().splitlines())


def governance_drift(cluster: Cluster) -> str | None:
    """Whether the live governance document still says what the deployed release says it should.

    The agent's identity can write ConfigMaps, because Helm keeps its release records there and RBAC
    cannot carve a single name out of that. The same access therefore reaches the trust's governance
    document, which decides cohort disclosure and the FLARE privacy policy. The agent has no reason to
    touch it — and this is how a site finds out if it, or anything else holding that access, ever did:
    compare the ConfigMap against the document in the release's own values before reporting an upgrade
    as done. A drifted document stops the tick reporting SUCCEEDED; it does not move the release,
    because the document was not changed by the upgrade and rolling back would quietly revert whatever
    the site meant to say.

    None means there is nothing to compare: a trust with no governance document, or values without one.
    """
    env = {**os.environ, "HELM_DRIVER": HELM_DRIVER}
    values = run(["helm", "get", "values", cluster.release, "-n", cluster.namespace, "-o", "json"], Path("/"), env=env)
    if values.returncode != 0:
        return f"could not read the release values to check the governance document:\n{values.stderr[-400:]}"
    try:
        wanted = (json.loads(values.stdout or "{}").get("governance") or {}).get("document") or ""
    except json.JSONDecodeError:
        return "the release values are not JSON, so the governance document could not be checked"
    if not wanted.strip():
        return None
    live = run(
        [
            "kubectl",
            "get",
            "configmap",
            "-n",
            cluster.namespace,
            "-l",
            f"app.kubernetes.io/component={GOVERNANCE_COMPONENT}",
            "-o",
            "json",
        ],
        Path("/"),
    )
    if live.returncode != 0:
        return f"could not read the governance ConfigMap:\n{live.stderr[-400:]}"
    try:
        items = json.loads(live.stdout or "{}").get("items", [])
    except json.JSONDecodeError:
        return "kubectl returned something other than JSON for the governance ConfigMap"
    if not items:
        return "the release carries a governance document but the cluster has no governance ConfigMap"
    if len(items) > 1:
        return f"{len(items)} ConfigMaps carry the governance component label; a trust has one"
    have = _document((items[0].get("data") or {}).get(GOVERNANCE_KEY, ""))
    want = _document(wanted)
    if have != want:
        return (
            "the governance document in the cluster does not match the one the release carries, so it was "
            f"changed outside Helm (live sha256 {hashlib.sha256(have.encode()).hexdigest()[:12]}, release "
            f"sha256 {hashlib.sha256(want.encode()).hexdigest()[:12]})"
        )
    return None


# The manual upgrade's own rollout judge (trust/deploy/helm/scripts/rollout_wait.py), copied into the
# agent image beside this script, so both paths wait on the same core workloads (FLIP#1645).
ROLLOUT_WAIT = Path(__file__).resolve().parent / "rollout_wait.py"
if not ROLLOUT_WAIT.exists():  # a checkout rather than the image
    ROLLOUT_WAIT = Path(__file__).resolve().parent.parent / "trust" / "deploy" / "helm" / "scripts" / "rollout_wait.py"


def ready_fl_clients(cluster: Cluster) -> str:
    """The fl-client workloads ready before the upgrade, which ``wait_core_rollout`` then waits on too.

    Best effort: an empty answer only means no fl-client is held to coming back, as on the manual path.
    """
    out = run(
        [
            sys.executable,
            str(ROLLOUT_WAIT),
            "--namespace",
            cluster.namespace,
            "--release",
            cluster.release,
            "--report-ready",
        ],
        Path.cwd(),
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def wait_core_rollout(cluster: Cluster, ready_before: str, timeout_s: int = VERIFY_TIMEOUT_S) -> tuple[bool, str]:
    """Wait until every core workload of the release has rolled out, as ``upgrade-trust-k8s`` does.

    trust-api answering on the target says nothing about imaging-api, data-access-api or the databases,
    so a release whose other pods never become ready would otherwise be reported SUCCEEDED.
    """
    budget = remaining_budget()
    if budget is not None:
        timeout_s = min(timeout_s, int(budget))
    out = run(
        [
            sys.executable,
            str(ROLLOUT_WAIT),
            "--namespace",
            cluster.namespace,
            "--release",
            cluster.release,
            "--timeout",
            f"{timeout_s}s",
            "--ready-before",
            ready_before,
        ],
        Path.cwd(),
    )
    text = (out.stdout + out.stderr)[-LOG_TAIL_BYTES:]
    if out.returncode == 0:
        return True, "every core workload rolled out"
    return False, f"the upgrade applied but not every core workload rolled out within {timeout_s}s:\n{text}"


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
    budget = remaining_budget()
    if budget is not None:
        timeout_s = min(timeout_s, int(budget))
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


def stopped_note(previous: str | None, failed: str) -> str:
    """What the failure report says about the way back, which is left to an operator.

    The agent never moves a site back by itself. An upgrade recreates the database-bearing services
    and runs XNAT's in-place schema upgrade, so once one has got that far the previous release's images
    may no longer match the data. Redeploying them anyway is the case to avoid, and the agent cannot
    tell from outside whether this attempt reached a migration. It stops and says where the site was.
    """
    was = f"on {previous}" if previous and previous != failed else "on an unknown release"
    return (
        f"Stopped; nothing was rolled back. The site was {was} before this attempt and may now be partly on "
        f"{failed}. An operator restores it (Upgrading sites: rolling back) and then clears the failure."
    )


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
    """Re-posts STARTING while the upgrade runs, so an upgrade slower than the lease cannot lose it.

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


def tick(cluster: Cluster, dry_run: bool) -> int:
    """One tick: read the policy, decide, and if due, claim, upgrade, verify and report."""
    try:
        kit = cluster_kit(cluster)
        _, policy = hub_call(kit, "GET", "/trust/upgrade-policy")
    except (OSError, KeyError, ValueError, urllib.error.URLError) as exc:
        print(f"cannot read the policy: {exc}", file=sys.stderr)
        return 2
    assert policy is not None
    state = cluster_state(cluster)
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
    ready_before = ready_fl_clients(cluster)
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
            ok, tail = cluster_upgrade(cluster, decision.release)
            detail = tail
            if ok:
                # helm exiting 0 only says the release was applied. Confirm every core workload rolled
                # out and the site came back up ON the target before telling the hub it SUCCEEDED.
                ok, detail = wait_core_rollout(cluster, ready_before)
                print(detail)
            if ok:
                ok, detail = verify(kit, decision.release)
                print(detail)
            if not ok:
                # Either helm failed or the site did not come back on the target. Both can leave it
                # half-applied, possibly past a migration, so stop here and say so (see stopped_note).
                how = stopped_note(state.current_release, decision.release)
                detail = f"{detail}\n{how}"
                print(how)
            else:
                # The upgrade applied and the site came up. Before calling it done, check the release's
                # own governance document is still what the cluster holds (see governance_drift).
                drift = governance_drift(cluster)
                if drift:
                    ok, detail = False, drift
                    print(drift)
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
    t.add_argument("--dry-run", action="store_true", help="print the decision, change nothing")
    t.add_argument("--namespace", required=True, help="the trust's namespace")
    t.add_argument("--release", required=True, help="the Helm release name")
    t.add_argument("--chart-repository", required=True, help="e.g. oci://ghcr.io/londonaicentre/charts/flip-trust")
    t.add_argument("--kit-name", required=True, help="TRUST_CODE, for the hub's log line")
    t.add_argument("--hub", required=True, help="prod | stag (the kit token, not the URL)")
    t.add_argument(
        "--budget-seconds",
        type=int,
        help="finish (and report) within this many seconds; the chart passes the Job deadline minus a margin",
    )
    args = parser.parse_args(argv)
    if args.budget_seconds is not None:
        if args.budget_seconds <= REPORT_RESERVE_S:
            t.error(f"--budget-seconds must be more than the {REPORT_RESERVE_S}s kept for the report")
        set_budget(args.budget_seconds)
        signal.signal(signal.SIGTERM, _terminated)
    cluster = Cluster(args.namespace, args.release, args.chart_repository, args.kit_name, args.hub)
    return tick(cluster, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
