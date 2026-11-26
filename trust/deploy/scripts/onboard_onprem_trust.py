#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
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
"""On-prem trust readiness checklist.

Diagnoses what's still missing in the operator's setup before `make
up-onprem-trust KIT=<CODE>` will succeed: docker swarm state,
kit file presence, Hub-shared block, the hub API / FL endpoint / image
registries reachable from this host, Kit credentials, FL_KIT_DIR + its
on-disk contents, OMOP / Orthanc data dirs.

Each check renders ✅ (pass), ❌ (fail with concrete fix hint), or ⏳
(pending — depends on something earlier that hasn't been satisfied yet,
typically the kit file). Runs every check even when the kit file is
absent so a first-time operator can run `make onboard-onprem-trust`
straight after cloning the repo and see what they need to ask the FLIP
admin for.

Exits 0 when ready (warnings allowed), 1 when a check failed or is pending, 2 when
the checklist itself could not run. The root Makefile's
`up-onprem-trust` target wraps this script as a precheck so an operator
gets diagnostics instead of a cryptic compose / pydantic failure deeper
in the stack.

Usage:
    uv run trust/deploy/scripts/onboard_onprem_trust.py [KIT] [--kit-file PATH]
    # KIT defaults to Trust_2; without --kit-file, reads trust/.env.<KIT>.
    # Make passes its resolved kit path, including the selected environment suffix.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_upgrade  # noqa: E402 — resolves a CI-deployed hub's sha build to this checkout's release
import trust_checks  # noqa: E402 — the result type and report every trust check shares
import trust_kit_lib  # noqa: E402 — the one Python reader of TRUST_DATA_SEED
from trust_checks import Check, Status  # noqa: E402

WIDTH = 71


def quote_make_assignment(key: str, value: str | Path) -> str:
    """Escape Make's dollar expansion before quoting the assignment for the shell."""
    return shlex.quote(f"{key}={value}".replace("$", "$$"))


# Keys a kit may lack because every reader falls back: FL_EDGE_PORT (kits synced before FLIP#1365 lack it;
# readers fall back to FL_SERVER_PORT). Requiring such a key would FAIL every existing site's upgrade checklist.
_HUB_SHARED_OPTIONAL: frozenset[str] = frozenset({"FL_EDGE_PORT"})
# Derived from trust_kit_lib (the one list, held to register_trust.HUB_SHARED_ENV_KEYS by flip-api's lockstep
# test). A key added there becomes REQUIRED here for every existing kit; if older kits can lack it and readers
# fall back, add it to _HUB_SHARED_OPTIONAL in the same change.
HUB_SHARED_KEYS: tuple[str, ...] = tuple(k for k in trust_kit_lib.HUB_SHARED_KEYS if k not in _HUB_SHARED_OPTIONAL)
KIT_CRED_KEYS: tuple[str, ...] = (
    "TRUST_API_KEY",
    "TRUST_INTERNAL_SERVICE_KEY",
    "FL_KIT_SLOT",
    "FL_KIT_SLOT_NUMBER",
    "EXPECTED_TRUST_ID",
)

# Trust-local credentials the operator must rotate for a real on-prem
# deployment. Used by the soft-warning check that flags any password still
# matching the .example template verbatim. Usernames are deliberately
# excluded — service-account usernames legitimately stay as their template
# defaults (XNAT depends on `flipServiceAccount` etc.).
TRUST_LOCAL_PASSWORD_KEYS: tuple[str, ...] = (
    "ORTHANC_PASSWORD",
    "OMOP_POSTGRES_PASSWORD",
    "DATA_ACCESS_POSTGRES_PASSWORD",
    "XNAT_ADMIN_PASSWORD",
    "XNAT_SERVICE_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)

# ─────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────

# ANSI colour codes — empty strings when stdout isn't a tty so the output
# stays clean in CI logs / pipes.
_TTY = sys.stdout.isatty()
RESET = "\033[0m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
DIM = "\033[2m" if _TTY else ""
GREEN = "\033[32m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""


COLOUR = {Status.PASS: GREEN, Status.FAIL: RED, Status.PENDING: YELLOW, Status.WARN: YELLOW}


def rule(char: str = "═") -> None:
    print(char * WIDTH)


def heading(text: str) -> None:
    rule()
    print(f"  {BOLD}{text}{RESET}")
    rule()


def render_check(c: Check, label_width: int) -> None:
    """Render one check row + any indented hints."""
    label = f"{c.name}:".ljust(label_width)
    # ⚠️ renders narrow on some terminals, so it takes a trailing space.
    glyph = c.status.glyph + (" " if c.status is Status.WARN else "")
    print(f"  {COLOUR.get(c.status, '')}{glyph}{RESET} {label} {c.detail}")
    for hint in c.hints:
        print(f"       {DIM}→ {hint}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Kit-file helpers
# ─────────────────────────────────────────────────────────────────────


def read_kit_vars(kit_file: Path) -> dict[str, str]:
    """Parse trust/.env.<KIT> into a dict of KEY → value. Empty dict if absent.

    Comment + blank lines pass through; the first `=` splits each line. Trailing
    `=` chars in values (e.g. base64 padding) are preserved.
    """
    if not kit_file.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in kit_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def is_filled(value: str | None) -> bool:
    """A value is "filled" iff it's non-empty and not a `<run-make-…>` placeholder."""
    return bool(value) and "<run-make-" not in value


# ─────────────────────────────────────────────────────────────────────
# Environment probes (independent of kit file)
# ─────────────────────────────────────────────────────────────────────


def docker_swarm_state() -> str:
    """Return the local docker swarm state (`active`, `inactive`, etc.), `worker` for an active node that is
    not a manager (`docker stack deploy` needs one), `permission-denied`, or `unavailable`."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        if "permission denied" in (result.stderr or "").lower():
            return "permission-denied"
        return "unavailable"
    state, _, manager = result.stdout.strip().partition(" ")
    if state == "active" and manager != "true":
        return "worker"
    return state or "inactive"


def detect_host_gpu_count() -> int | None:
    """Best-effort count of NVIDIA GPUs visible to this host. None when undetectable.

    Returns 0 cleanly when nvidia-smi is absent — that's the unambiguous
    "no NVIDIA GPU exposed to Linux containers" signal on macOS (including
    Apple Silicon — Docker Desktop does not passthrough the integrated GPU)
    and on Linux hosts without the NVIDIA driver. Returns None only when
    nvidia-smi exists but errors, so the GPU-capacity check can avoid raising
    a false-positive warning.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--list-gpus"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return 0
    except subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    return sum(1 for line in result.stdout.splitlines() if line.strip())


# ─────────────────────────────────────────────────────────────────────
# Individual checks (each returns a Check)
# ─────────────────────────────────────────────────────────────────────


def check_swarm() -> Check:
    state = docker_swarm_state()
    if state == "active":
        return Check("Docker swarm", Status.PASS, "active on this host")
    if state == "permission-denied":
        return Check(
            "Docker swarm",
            Status.FAIL,
            "docker daemon is up but denied this user access",
            hints=[
                "This user cannot reach the Docker socket. On a host provisioned by the on-prem playbook"
                " that is deliberate (the docker group is root-equivalent): re-run via"
                " sudo -E make onboard-onprem-trust KIT=<CODE>. Otherwise ask the host admin for Docker"
                " access on a swarm manager — see 'Access the operator needs' in the on-prem deployment guide.",
            ],
        )
    if state == "worker":
        return Check(
            "Docker swarm",
            Status.FAIL,
            "active, but this node is a swarm worker: the XNAT stack deploy needs a manager",
            hints=["Run the trust on a swarm manager, or ask the swarm admin: docker node promote <this node>"],
        )
    if state == "unavailable":
        return Check(
            "Docker swarm",
            Status.FAIL,
            "docker not reachable (daemon down or not installed)",
            hints=["Install Docker, start the daemon, then run: docker swarm init"],
        )
    return Check(
        "Docker swarm",
        Status.FAIL,
        f"{state} (overlay networks require swarm mode)",
        hints=["One-off host setup: docker swarm init"],
    )


def check_kit_file(kit: str, kit_file: Path, kit_path: str | None = None) -> Check:
    kit_path = kit_path or str(kit_file)
    if kit_file.is_file():
        return Check("Kit file present", Status.PASS, kit_path)
    return Check(
        "Kit file MISSING",
        Status.FAIL,
        kit_path,
        hints=[
            "Ask the FLIP admin to package + send your kit (`make package-onprem-trust-kit",
            f"  KIT={kit} HUB={os.environ.get('HUB') or 'prod'}` from deploy/aws),",
            "  extract the tarball, then:",
            f"    cp <extracted-dir>/<kit-file> {kit_path}",
        ],
    )


def check_hub_shared(kit_vars: dict[str, str], kit_present: bool) -> Check:
    label = f"Hub-shared block ({len(HUB_SHARED_KEYS)} keys)"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    # The other backend's kit date may be empty (FLIP#1476): an NVFLARE hub has no FLOWER_KIT_DATE.
    other_kit_date = {"nvflare": "FLOWER_KIT_DATE", "flower": "FLARE_KIT_DATE"}.get(kit_vars.get("FL_BACKEND", ""))
    missing = [
        k
        for k in HUB_SHARED_KEYS
        if not is_filled(kit_vars.get(k)) and not (k == other_kit_date and not kit_vars.get(k))
    ]
    if not missing:
        hub_url = kit_vars.get("CENTRAL_HUB_API_URL", "").removesuffix("/api")
        return Check(
            label,
            Status.PASS,
            "populated",
            hints=[f"UI URL: {hub_url or '<unset>'}"],
        )
    return Check(
        label,
        Status.FAIL,
        f"{len(missing)} unfilled: {', '.join(missing)}",
        hints=["Ask the FLIP admin to run 'make sync-trust-kit KIT=<CODE>' and re-send the kit."],
    )


def fetch_local_trust_health(port: str, timeout: float = 3.0) -> dict:
    """``GET http://127.0.0.1:<port>/health`` of this host's trust-api, parsed.

    Raises whatever urllib raises when nothing answers — the caller maps that to PENDING.
    """
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"Accept": "application/json"})
    # No proxies: behind a forward proxy with NO_PROXY unset (a TRE), urllib would send loopback to the proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 — loopback only
        body = json.loads(response.read().decode() or "{}")
    return body if isinstance(body, dict) else {}


# ─────────────────────────────────────────────────────────────────────
# Outbound reachability (FLIP#1726). Probed from the operator's account before anything
# starts, so a firewall gap shows here, with the host:port to request, rather than as a failed
# heartbeat or a dead FL client later. The rows say "reachable from this host" and no more: a
# firewall that filters only one direction answers these probes while blocking nothing (#1684).
# ─────────────────────────────────────────────────────────────────────

PROBE_TIMEOUT_SECONDS = 4.0
# Both hosts of the one registry a trust pulls from (FLIP#1721): ghcr.io alone passes while every
# pull fails on the layer host.
REGISTRY_HOSTS: tuple[str, ...] = (
    "ghcr.io",
    "pkg-containers.githubusercontent.com",
)
EGRESS_DOC = "the egress list on docs → Deploy a trust node → Network requirements"
EGRESS_IP_URL = "https://checkip.amazonaws.com"


def _failure_reason(error: BaseException) -> str:
    """One short phrase an operator can put in a firewall request."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return _failure_reason(error.reason) if isinstance(error.reason, BaseException) else str(error.reason)
    if isinstance(error, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timed out (blackholed or filtered)"
    if isinstance(error, ConnectionRefusedError):
        return "connection refused"
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return f"{type(error).__name__}: {error}"


def probe_tcp(host: str, port: int, timeout: float = PROBE_TIMEOUT_SECONDS) -> str | None:
    """None when a TCP connection to host:port opens, else why it did not."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as error:
        return _failure_reason(error)


def lookup_egress_ip(timeout: float = PROBE_TIMEOUT_SECONDS) -> str | None:
    """The public address this host leaves from, or None when it cannot be looked up."""
    try:
        with urllib.request.urlopen(EGRESS_IP_URL, timeout=timeout) as response:
            return str(ipaddress.ip_address(response.read(64).decode("ascii").strip()))
    except Exception:  # only feeds a hint: a blocked or intercepted lookup must not fail the checklist
        return None


def probe_hub_health(hub_url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[str | None, str | None]:
    """(release the hub reports, None) when ``GET <hub_url>/health`` answers, else (None, why not)."""
    try:
        return site_upgrade.fetch_hub_version(hub_url, timeout=timeout), None
    except ValueError:
        # The hub's CloudFront answers any unknown path with the UI's HTML and a 200, so a kit whose
        # URL lacks `/api` gets a page, not the health JSON.
        return None, "answered, but not with the health JSON — is CENTRAL_HUB_API_URL the API URL (ending /api)?"
    except (urllib.error.URLError, OSError) as error:
        return None, _failure_reason(error)


def check_hub_reachable(kit_vars: dict[str, str], kit_present: bool) -> Check:
    label = "Hub API reachable"
    hub_url = kit_vars.get("CENTRAL_HUB_API_URL", "")
    if not (kit_present and is_filled(hub_url)):
        return Check(label, Status.PENDING, "pending — needs CENTRAL_HUB_API_URL in the kit file")
    release, failure = probe_hub_health(hub_url)
    if failure is None:
        return Check(label, Status.PASS, f"{hub_url}/health answers" + (f", hub runs {release}" if release else ""))
    return Check(
        label,
        Status.FAIL,
        f"{hub_url}/health: {failure}",
        hints=[
            "trust-api polls this URL on 443 (HTTPS); nothing starts without it.",
            f"Ask the network team to allow it outbound from this host — {EGRESS_DOC}.",
        ],
    )


def fl_endpoint(kit_vars: dict[str, str]) -> tuple[str, int, str] | None:
    """(host, port, where it came from) the FL client will dial, or None when the kit cannot say yet.

    FLARE dials the ``target`` in its kit slice's ``startup/fed_client.json``, so that wins when the
    slice is on disk; otherwise, and always for Flower (whose compose passes exactly these), the
    kit's ``NLB_SUBDOMAIN`` and ``FL_EDGE_PORT`` (``FL_SERVER_PORT`` for a kit synced before FLIP#1365).
    The port is the site's ``FL_EDGE_PORT_OVERRIDE``, else ``FL_EDGE_PORT``, when set: fl-client-init
    writes that port into FLARE's ``fed_client.json`` before the client starts (FLIP#2051).
    """
    ports = (kit_vars.get("FL_EDGE_PORT_OVERRIDE", ""), kit_vars.get("FL_EDGE_PORT", ""))
    dial = next((p for p in ports if p.isdigit()), "")
    if kit_vars.get("FL_BACKEND") == "nvflare":
        fed_client = (
            Path(kit_vars.get("FL_KIT_DIR", "") or "/nonexistent")
            / "net-1"
            / "services"
            / (kit_vars.get("FL_KIT_SLOT", "") or "unset")
            / "startup"
            / "fed_client.json"
        )
        try:
            target = json.loads(fed_client.read_text())["servers"][0]["service"]["target"]
            host, _, port = str(target).rpartition(":")
            if host and port.isdigit():
                return host, int(dial or port), "startup/fed_client.json"
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            pass
    host = kit_vars.get("NLB_SUBDOMAIN", "")
    port = dial or kit_vars.get("FL_SERVER_PORT", "")
    if is_filled(host) and port.isdigit():
        return host, int(port), "the kit's NLB_SUBDOMAIN"
    return None


def check_fl_endpoint_reachable(kit_vars: dict[str, str], kit_present: bool) -> Check:
    label = "FL endpoint reachable"
    endpoint = fl_endpoint(kit_vars) if kit_present else None
    if endpoint is None:
        return Check(label, Status.PENDING, "pending — needs the kit's NLB_SUBDOMAIN and FL port")
    host, port, source = endpoint
    failure = probe_tcp(host, port)
    if failure is None:
        return Check(label, Status.PASS, f"tcp {host}:{port} connects (from {source})")
    hints = [
        "The FL client connects out to this host:port over TLS; a connection that opens"
        " here but fails in the client log is a TLS-inspection proxy re-signing it — exempt the host.",
        f"Ask the network team to allow it outbound from this host — {EGRESS_DOC}.",
    ]
    if "timed out" in failure:
        # A hub that filters by source address drops an unlisted site without an answer.
        egress_ip = lookup_egress_ip()
        leaves_from = f": {egress_ip}" if egress_ip else " (not found from here; your network team knows it)"
        hints.append(
            "If outbound is already allowed, the hub may admit FL connections only from registered site"
            f" addresses — send the FLIP hub team the public address this host leaves from{leaves_from}."
        )
    return Check(label, Status.FAIL, f"tcp {host}:{port}: {failure} (from {source})", hints=hints)


def check_registries_reachable() -> Check:
    """WARN, never FAIL: an offline image mirror is a supported shape (FLIP#1721)."""
    label = "Image registries reachable"
    with ThreadPoolExecutor(max_workers=len(REGISTRY_HOSTS)) as pool:
        failures = dict(zip(REGISTRY_HOSTS, pool.map(lambda host: probe_tcp(host, 443), REGISTRY_HOSTS)))
    unreachable = {host: why for host, why in failures.items() if why is not None}
    if not unreachable:
        return Check(label, Status.PASS, f"tcp 443 to {len(REGISTRY_HOSTS)} registry hosts connects")
    return Check(
        label,
        Status.WARN,
        "; ".join(f"{host}: {why}" for host, why in unreachable.items()),
        hints=[
            "ghcr.io + pkg-containers.githubusercontent.com serve every image a trust runs: FLIP's own and"
            " its mirrors of the third-party ones (FLIP#1721). Pulls need both hosts.",
            f"Allow them outbound ({EGRESS_DOC}), or agree an image mirror with the FLIP team (FLIP#1721).",
        ],
    )


def kit_key_fingerprint(kit_vars: dict[str, str]) -> str | None:
    """The kit's AES key digest, computed as the hub and trust-api do (SHA-256 of the decoded key, 12 hex)."""
    raw = kit_vars.get("AES_KEY_BASE64", "")
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:  # binascii.Error included
        return None
    return hashlib.sha256(key).hexdigest()[:12] if key else None


def check_hub_shared_current(kit_vars: dict[str, str], kit_present: bool) -> Check:
    """Whether the kit's Hub-shared block still matches the hub — asked of the running trust-api.

    Every heartbeat reply carries a fingerprint of the hub's AES key and the release the hub
    runs (FLIP#1204); trust-api reports the hub's fingerprint and whether its own running key
    matches it. The kit is judged by its own key, not the running one: after the operator
    replaces a stale Hub-shared block, trust-api keeps the old key until it is recreated — and
    the upgrade this checklist gates is what recreates it, so failing on the running key would
    lock the fix out. Only a kit whose key differs from the hub's FAILs. With no kit file the
    row is PENDING; everything else short of a stale kit is a WARN — a first install has no
    trust-api to ask yet, and a site built before FLIP#1204 runs a trust-api that cannot answer.
    A kit pinned behind the hub's release is a WARN naming the upgrade verb.
    """
    label = "Hub-shared block current"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    port = kit_vars.get("TRUST_API_PORT", "8020")
    try:
        health = fetch_local_trust_health(port)
    except Exception:
        return Check(
            label,
            Status.WARN,
            f"not verified — trust-api not answering on 127.0.0.1:{port} (checked once the stack is up)",
        )
    key_match = health.get("hub_key_match")
    hub_version = health.get("hub_version")
    hub_fingerprint = health.get("hub_key_fingerprint")
    if key_match is None or not isinstance(hub_fingerprint, str):
        return Check(
            label,
            Status.WARN,
            "not verified — trust-api has not heard back from the hub yet, or predates FLIP#1204 "
            "(upgrading it fixes that)",
        )
    stale_hints = [
        "The Hub-shared block is stale. Ask the FLIP admin for a refreshed kit:",
        (
            "  make sync-trust-kit KIT=<CODE> HUB=<env>  →  "
            "make -C deploy/aws package-onprem-trust-kit KIT=<CODE> HUB=<env>"
        ),
        "then replace ONLY the Hub-shared block in your kit file and re-run the upgrade.",
    ]
    kit_fingerprint = kit_key_fingerprint(kit_vars)
    if kit_fingerprint is None:
        return Check(
            label,
            Status.FAIL,
            "the kit's AES_KEY_BASE64 is not a valid base64 key",
            hints=["The Hub-shared block is damaged. Replace it from the kit the FLIP admin sent."],
        )
    if kit_fingerprint != hub_fingerprint:
        return Check(
            label,
            Status.FAIL,
            "this kit's AES key differs from the hub's — every task will fail to decrypt",
            hints=stale_hints,
        )
    if key_match is False:
        return Check(
            label,
            Status.WARN,
            "the kit carries the hub's AES key; the running trust-api still has the old one — the upgrade recreates it",
        )
    pinned = kit_vars.get("DOCKER_TAG", "")
    # A hub deployed by the CI Terraform apply reports the sha build of its release commit.
    hub_release = (site_upgrade.release_for_sha(hub_version) or hub_version) if isinstance(hub_version, str) else None
    if hub_release and pinned and hub_release != pinned:
        return Check(
            label,
            Status.WARN,
            f"key matches the hub; kit pins {pinned}, hub runs {hub_release}",
            hints=["Move this site to the hub's release: make upgrade-onprem-trust KIT=<CODE>"],
        )
    return Check(label, Status.PASS, f"key matches the hub (hub runs {hub_release or 'an unreported release'})")


def check_kit_credentials(kit_vars: dict[str, str], kit_present: bool, kit: str, kit_path: str | None = None) -> Check:
    kit_path = kit_path or f"trust/.env.{kit}"
    if not kit_present:
        return Check("Kit credentials (5 keys)", Status.PENDING, "pending — needs kit file")
    missing = [k for k in KIT_CRED_KEYS if not is_filled(kit_vars.get(k))]
    if not missing:
        return Check("Kit credentials (5 keys)", Status.PASS, "populated")
    return Check(
        "Kit credentials (5 keys)",
        Status.FAIL,
        f"{len(missing)} unfilled: {', '.join(missing)}",
        hints=[
            "Ask the FLIP admin to UI-register your trust (Add Trust modal),",
            f"  paste the 5 lines into {kit_path}, and re-send the kit.",
        ],
    )


def check_expected_trust_id_self_check(
    kit_vars: dict[str, str], kit_present: bool, kit: str, kit_path: str | None = None
) -> Check:
    """Enforce that the kit declares EXPECTED_TRUST_ID for the wrong-host self-check.

    trust-api's task_poller compares the trust id the hub returns on first
    heartbeat against EXPECTED_TRUST_ID and exits if they differ — the loud
    fail-stop that catches a wrong-kit-to-wrong-host deployment. The guard is
    opt-in (it only fires when the env var is set), so missing or placeholder
    EXPECTED_TRUST_ID silently disables the safety net.

    check_kit_credentials already flags this as part of its 5-key bundle, but
    we surface it as its own check so the operator sees the specific risk in
    the readiness output rather than "1 of 5 keys missing".
    """
    kit_path = kit_path or f"trust/.env.{kit}"
    if not kit_present:
        return Check("EXPECTED_TRUST_ID self-check", Status.PENDING, "pending — needs kit file")
    raw = kit_vars.get("EXPECTED_TRUST_ID", "")
    if not is_filled(raw):
        return Check(
            "EXPECTED_TRUST_ID self-check",
            Status.FAIL,
            "unset — wrong-host safety check disabled",
            hints=[
                "Without EXPECTED_TRUST_ID, a kit deployed to the wrong host will",
                "  silently act as the wrong trust until something downstream breaks.",
                "Re-register on the hub side and re-send the kit, OR ask the admin to",
                f"  fill the value into {kit_path} before bringing the stack up.",
            ],
        )
    return Check("EXPECTED_TRUST_ID self-check", Status.PASS, f"set to {raw}")


def check_fl_kit_dir_set(kit_vars: dict[str, str], kit_present: bool, kit: str, kit_path: str | None = None) -> Check:
    kit_path = kit_path or f"trust/.env.{kit}"
    if not kit_present:
        return Check("FL_KIT_DIR set", Status.PENDING, "pending — needs kit file")
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")
    if fl_kit_dir:
        return Check("FL_KIT_DIR set", Status.PASS, fl_kit_dir)
    return Check(
        "FL_KIT_DIR set",
        Status.FAIL,
        "not set in kit file",
        hints=[f"Add FL_KIT_DIR=<absolute path> to {kit_path}"],
    )


def check_fl_kit_dir_exists(fl_kit_dir: str, kit_present: bool) -> Check:
    if not kit_present:
        return Check("FL_KIT_DIR on disk", Status.PENDING, "pending — needs kit file")
    if not fl_kit_dir:
        return Check("FL_KIT_DIR on disk", Status.PENDING, "pending — FL_KIT_DIR unset")
    if Path(fl_kit_dir).is_dir():
        return Check("FL_KIT_DIR on disk", Status.PASS, fl_kit_dir)
    return Check(
        "FL_KIT_DIR on disk",
        Status.FAIL,
        f"{fl_kit_dir} (does not exist)",
        hints=[
            "Extract the FL kit tarball from the FLIP admin at this path,",
            "  preserving the net-1/ hierarchy.",
        ],
    )


def _client_cannot_read(path: Path, kit_vars: dict[str, str], image_uid: int) -> str:
    """Why the FL client's uid could not read this kit file, or "" when it can (FLIP#1731).

    The kit's private key is 600/660 to whoever extracted it; the client runs as FLIP_UID, else the
    image's uid, and the operator cannot test another uid's access, so judge owner, group and mode.
    The key's directory is the bind-mount root, so the client must also enter it; the host dirs above
    it are resolved by the daemon and never walked by the client. A non-numeric id is the Container
    uid row's to report, so it is skipped here rather than raised.
    """
    raw_uid = kit_vars.get("FLIP_UID", "").strip()
    raw_gid = kit_vars.get("FLIP_GID", "").strip() or raw_uid
    if not (raw_uid or "0").isdigit() or not (raw_gid or "0").isdigit():
        return ""
    uid, gid = int(raw_uid or image_uid), int(raw_gid or image_uid)

    def allowed(p: Path, owner_bit: int) -> bool:
        st = p.stat()
        bit = owner_bit if st.st_uid == uid else owner_bit >> 3 if st.st_gid == gid else owner_bit >> 6
        return bool(st.st_mode & bit)

    try:
        if not allowed(path.parent, 0o100):
            mode = path.parent.stat().st_mode & 0o777
            return f"{path.parent} (mode {mode:o}) cannot be entered by the client, which runs as uid {uid}"
        if allowed(path, 0o400):
            return ""
        st = path.stat()
    except OSError:
        return ""
    return f"{path} is owned by uid {st.st_uid} mode {st.st_mode & 0o777:o}, and the client runs as uid {uid}"


_KIT_AS_ROOT_HINT = (
    "That is how it should be when the FL client runs as that uid. To check inside it, run the checklist as root:"
    " sudo -E make onboard-onprem-trust KIT=<CODE>."
)


def _sealed_from_me(root: Path, target: Path) -> str:
    """Why this user cannot look inside the kit on the way from ``root`` to ``target``, or "" when it can (FLIP#1964).

    The kit is private to the FL client's uid, so an operator who is another uid, without sudo, cannot list it, and
    everything inside would look missing. That is the kit as it should be, not a missing one. Each folder is asked
    of os.access from the top down, never of pathlib: below a closed folder, is_dir() raises before Python 3.14 and
    answers False from 3.14 on.
    """
    relative = target.relative_to(root)
    for folder in (root / p for p in (*reversed(relative.parents), relative)):
        try:
            if not os.path.lexists(folder):  # missing, which the contents check reports
                return ""
            if not os.access(folder, os.R_OK | os.X_OK):
                st = folder.stat()
                mode = st.st_mode & 0o777
                return f"{folder} belongs to uid {st.st_uid} (mode {mode:o}), so uid {os.getuid()} cannot look in"
        except OSError:
            return ""
    return ""


def check_fl_kit_contents(kit_vars: dict[str, str], kit_present: bool) -> Check:
    if not kit_present:
        return Check("FL kit contents", Status.PENDING, "pending — needs kit file")
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")
    backend = kit_vars.get("FL_BACKEND", "")
    slot = kit_vars.get("FL_KIT_SLOT", "")
    slot_number = kit_vars.get("FL_KIT_SLOT_NUMBER", "")
    if not (fl_kit_dir and backend and slot):
        return Check(
            "FL kit contents",
            Status.PENDING,
            "pending — FL_KIT_DIR / FL_BACKEND / FL_KIT_SLOT not all set in kit file",
        )
    if not Path(fl_kit_dir).is_dir():
        return Check(
            "FL kit contents",
            Status.PENDING,
            f"pending — waiting on FL_KIT_DIR ({fl_kit_dir}) to exist on disk",
        )
    root = Path(fl_kit_dir)
    if backend == "nvflare":
        target = root / "net-1" / "services" / slot
        if sealed := _sealed_from_me(root, target):
            return Check("FL kit contents (nvflare)", Status.WARN, f"not checked: {sealed}", hints=[_KIT_AS_ROOT_HINT])
        sub = {p: (target / p).is_dir() for p in ("local", "startup", "transfer")}
        if all(sub.values()):
            if why := _client_cannot_read(target / "startup" / "client.key", kit_vars, 1000):
                return Check(
                    "FL kit contents (nvflare)",
                    Status.WARN,
                    f"{target}/{{local,startup,transfer}}; {why}",
                    hints=[f"chown -R <that uid> {target} (the client also writes local/ and transfer/)."],
                )
            return Check(
                "FL kit contents (nvflare)",
                Status.PASS,
                f"{target}/{{local,startup,transfer}}",
            )
        missing = ", ".join(p for p, ok in sub.items() if not ok)
        return Check(
            "FL kit contents (nvflare)",
            Status.FAIL,
            f"missing {missing} under {target}",
            hints=["Check the tarball was extracted preserving the net-1/services/<slot>/ hierarchy."],
        )
    # flower
    certs = root / "net-1" / "certificates"
    creds = root / "net-1" / "keys" / f"supernode_credentials_{slot_number}"
    if sealed := _sealed_from_me(root, creds.parent):
        return Check("FL kit contents (flower)", Status.WARN, f"not checked: {sealed}", hints=[_KIT_AS_ROOT_HINT])
    if certs.is_dir() and creds.is_file():
        if why := _client_cannot_read(creds, kit_vars, 49999):
            return Check(
                "FL kit contents (flower)",
                Status.WARN,
                f"{certs}/ + supernode_credentials_{slot_number}; {why}",
                hints=[f"chown <that uid> {creds}, or chmod 640 it with the client's group."],
            )
        return Check(
            "FL kit contents (flower)",
            Status.PASS,
            f"{certs}/ + supernode_credentials_{slot_number}",
        )
    missing_parts = []
    if not certs.is_dir():
        missing_parts.append(f"{certs}/")
    if not creds.is_file():
        missing_parts.append(str(creds))
    return Check(
        "FL kit contents (flower)",
        Status.FAIL,
        f"missing: {' '.join(missing_parts)}",
        hints=["Check the tarball was extracted preserving the net-1/{certificates,keys}/ hierarchy."],
    )


def check_gpu_capacity(kit_vars: dict[str, str], kit_present: bool, kit: str, kit_path: str | None = None) -> Check:
    """Warn when the kit claims more GPUs than the host actually exposes.

    The fl-client container expands resources.json from a template using
    NUM_AVAILABLE_GPUS at start-up. If the kit says e.g. 1 but the host has 0
    (Mac without GPU passthrough, CPU-only Linux box, etc.), nvflare crashes
    with `ValueError: num_of_gpus specified (N) exceeds available GPUs: 0.`
    before it ever dials the FL server — and the failure is opaque in the
    fl-client logs. Surface it here so the operator fixes it pre-launch.

    Soft WARN (not FAIL) because the operator can knowingly bring the stack
    up with a stale value and just tolerate the fl-client crash-loop while
    they iterate; the rest of the trust services come up regardless.
    """
    kit_path = kit_path or f"trust/.env.{kit}"
    if not kit_present:
        return Check("fl-client GPU capacity", Status.PENDING, "pending — needs kit file")
    raw = (kit_vars.get("NUM_AVAILABLE_GPUS") or "").strip()
    if not raw:
        if kit_vars.get("FL_BACKEND", "").strip().lower() != "nvflare":
            return Check(
                "fl-client GPU capacity",
                Status.PASS,
                "NUM_AVAILABLE_GPUS unset in kit (CPU-only; only the FLARE client reads it)",
            )
        # `make up-trust` applies the GPU passthrough overlay only when the kit sets
        # NUM_AVAILABLE_GPUS > 0 (trust/Makefile GPU_OVERRIDE), so an unset value gets no
        # device — yet fl-client's entrypoint.sh resolves that same unset value to 1
        # (`${NUM_AVAILABLE_GPUS:-1}`) and FLARE then demands a GPU it was never given.
        # That crash-loops whatever the host carries, so there is no host count worth probing.
        return Check(
            "fl-client GPU capacity",
            Status.WARN,
            "NUM_AVAILABLE_GPUS unset in kit: the GPU overlay is skipped but fl-client defaults to 1 GPU",
            hints=[
                "fl-client will crash-loop on `num_of_gpus specified (1) exceeds available GPUs: 0`.",
                f"Edit {kit_path} → set NUM_AVAILABLE_GPUS explicitly: 0 (with MEMORY_PER_GPU_IN_GIB=0)",
                "  for CPU-only, or N on a host exposing N NVIDIA GPU(s) to enable passthrough.",
            ],
        )
    try:
        kit_gpus = int(raw)
    except ValueError:
        return Check(
            "fl-client GPU capacity",
            Status.FAIL,
            f"NUM_AVAILABLE_GPUS='{raw}' is not an integer",
            hints=[f"Edit {kit_path} → Trust-local credentials section."],
        )
    if kit_gpus <= 0:
        return Check(
            "fl-client GPU capacity",
            Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus} (CPU-only)",
        )
    host_gpus = detect_host_gpu_count()
    if host_gpus is None:
        return Check(
            "fl-client GPU capacity",
            Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus}; host GPU count undetectable (nvidia-smi errored)",
        )
    if host_gpus >= kit_gpus:
        return Check(
            "fl-client GPU capacity",
            Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus} ≤ host NVIDIA GPUs ({host_gpus})",
        )
    return Check(
        "fl-client GPU capacity",
        Status.WARN,
        f"NUM_AVAILABLE_GPUS={kit_gpus} but host exposes {host_gpus} NVIDIA GPU(s)",
        hints=[
            "fl-client will crash-loop on `num_of_gpus specified exceeds available GPUs`.",
            f"Edit {kit_path} → set NUM_AVAILABLE_GPUS=0 and MEMORY_PER_GPU_IN_GIB=0",
            "  for CPU-only (slow but functional), or move to a host with the expected GPU(s).",
        ],
    )


def _governance_document(kit_vars: dict[str, str], repo_root: Path) -> str | None:
    """The kit's ACCESS_POLICY_FILE as an absolute path, resolved as Compose resolves it.

    Compose runs with ``--project-directory trust``, so a relative path is relative to trust/.
    """
    raw = kit_vars.get("ACCESS_POLICY_FILE", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return str(path if path.is_absolute() else (repo_root / "trust" / path).resolve())


def check_site_privacy_policy(
    kit_vars: dict[str, str],
    kit_present: bool,
    kit: str,
    repo_root: Path,
    kit_path: str | None = None,
) -> Check:
    """Validate the site privacy filter with the same stdlib-only renderer used at runtime.

    Both sources: the kit's FL_SITE_PRIVACY_* and the governance document's
    [fl_privacy.nvflare] section (FLIP#1259). The renderer itself fails a filter set in both,
    and — told the backend — a filter on a backend that would not enforce it.
    """
    kit_path = kit_path or f"trust/.env.{kit}"
    label = "Site privacy policy"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    hints = [
        f"Edit {kit_path} (Host-local profile) or the governance document's [fl_privacy.nvflare];"
        " the fl-client fails closed on an invalid policy."
    ]
    env = {key: value for key, value in kit_vars.items() if key.startswith("FL_SITE_PRIVACY_")}
    document = _governance_document(kit_vars, repo_root)
    if document is not None:
        env["ACCESS_POLICY_FILE"] = document
    backend = kit_vars.get("FL_BACKEND", "").strip().lower() or "nvflare"

    renderer = repo_root / "flip-utils" / "flip" / "nvflare" / "site_policy.py"
    # The validation target must not exist: the renderer treats any existing file as a
    # stale render, so os.devnull would make a no-policy kit report "would remove stale
    # /dev/null". env stays kit-only on purpose — the check validates what the kit file
    # provides, not whatever the operator's shell happens to export.
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = subprocess.run(
            [
                sys.executable,
                str(renderer),
                "--check",
                "--fl-backend",
                backend,
                str(Path(tmp_dir) / "privacy.json"),
            ],
            capture_output=True,
            check=False,
            env=env,
            text=True,
        )
    if result.returncode:
        detail = result.stderr.strip().removeprefix("[site-privacy] FATAL: ")
        return Check(label, Status.FAIL, detail or "site privacy validation failed", hints=hints)
    # Keep the verdict half of the renderer's report; the rest names the throwaway
    # validation path, which means nothing in a readiness table.
    detail = result.stdout.strip().removeprefix("[site-privacy] ").split(" — ")[0]
    return Check(label, Status.PASS, detail)


def check_governance_document(
    kit_vars: dict[str, str],
    kit_present: bool,
    kit: str,
    repo_root: Path,
    kit_file: Path | None = None,
) -> Check:
    """Validate the governance document's [disclosure]/[access] half with data-access-api's loader.

    data-access-api refuses to start on an invalid document, and this checklist gates
    ``upgrade-onprem-trust``, so the error belongs here rather than in a crash-looping
    container. The loader is stdlib-only; it runs on this interpreter with the service tree on
    PYTHONPATH, installing nothing.

    The detailed-check command preserves the selected kit file instead of resolving it again.
    """
    label = "Governance document"
    kit_file = kit_file or repo_root / "trust" / f".env.{kit}"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    document = _governance_document(kit_vars, repo_root)
    floor = kit_vars.get("COHORT_QUERY_THRESHOLD", "").strip() or "10"
    if document is None:
        return Check(label, Status.PASS, f"not configured — platform defaults (COHORT_QUERY_THRESHOLD={floor})")

    service_root = repo_root / "trust" / "data-access-api"
    result = subprocess.run(
        [sys.executable, str(service_root / "data_access_api" / "scripts" / "check_governance.py")],
        capture_output=True,
        check=False,
        env={"ACCESS_POLICY_FILE": document, "COHORT_QUERY_THRESHOLD": floor, "PYTHONPATH": str(service_root)},
        text=True,
    )
    lines = result.stdout.strip().splitlines()
    if result.returncode:
        detail = (lines[0] if lines else result.stderr.strip()).removeprefix("❌ ")
        command = (
            f"make -C trust check-governance {quote_make_assignment('KIT', kit)} "
            f"{quote_make_assignment('KIT_FILE', kit_file)}"
        )
        if hub := os.environ.get("HUB"):
            command += f" {quote_make_assignment('HUB', hub)}"
        return Check(
            label,
            Status.FAIL,
            detail or "governance document validation failed",
            hints=[
                f"Edit {document}; data-access-api refuses to start on an invalid document.",
                f"`{command}` validates both halves with the details.",
            ],
        )
    facts = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in lines if ":" in line}
    digest = facts.get("sha256", "").split(" ")[0][:12]
    return Check(
        label,
        Status.PASS,
        f"valid — {facts.get('access rules', '0')} access rule(s), "
        f"min cohort size {facts.get('effective min cohort size', floor)}, sha256 {digest}",
    )


def check_unrotated_passwords(
    kit_vars: dict[str, str],
    kit_present: bool,
    repo_root: Path,
    kit: str,
    kit_path: str | None = None,
) -> Check:
    """Refuse a stag/prod kit whose Trust-local passwords still match the template defaults.

    `make new-trust` mints these, but a kit scaffolded from an older template, or edited by
    hand, can still carry trust/.env.example's mock-stack values. On a development kit that
    is a WARN (the defaults are usable for testing); on a deployed kit (any non-empty HUB) it is a FAIL,
    since the stack publishes Orthanc, Grafana and the OMOP database on the host.
    """
    kit_path = kit_path or f"trust/.env.{kit}"
    if not kit_present:
        return Check("Trust-local passwords", Status.PENDING, "pending — needs kit file")
    template = repo_root / "trust" / ".env.example"
    if not template.is_file():
        return Check("Trust-local passwords", Status.WARN, "not checked — trust/.env.example is missing")
    template_vars = read_kit_vars(template)
    # A key set to nothing was not rotated either; one the kit does not carry at all is not this check's.
    unchanged = [
        key
        for key in TRUST_LOCAL_PASSWORD_KEYS
        if key in kit_vars and (not kit_vars[key] or kit_vars[key] == template_vars.get(key))
    ]
    if not unchanged:
        return Check("Trust-local passwords", Status.PASS, "all rotated from template defaults")
    hub = os.environ.get("HUB", "prod")
    deployed = hub != ""
    return Check(
        "Trust-local passwords",
        Status.FAIL if deployed else Status.WARN,
        f"{len(unchanged)}/{len(TRUST_LOCAL_PASSWORD_KEYS)} are empty or still match {template.name} defaults",
        hints=[
            f"Unchanged or empty: {', '.join(unchanged)}",
            f"Edit {kit_path} → Trust-local credentials section and replace each with a",
            "  random secret (`make new-trust` mints them for a fresh kit).",
            f"Judged by HUB={hub}; run with HUB= for a development kit.",
        ],
    )


def check_log_level(kit_vars: dict[str, str], kit_present: bool, kit_path: str) -> Check:
    """Refuse a deployed kit left at ``TRUST_LOG_LEVEL=DEBUG`` (FLIP-SR-050).

    At DEBUG the trust APIs log request headers and bodies into Loki, which runs with auth
    disabled, shares the trust network with the FL client, and keeps 30 days of history — so the
    trust's hub API key, internal key and cohort-admin proof land where researcher FL code can read
    them. On a deployed kit (any non-empty HUB) that is a FAIL; on a development kit it is a WARN
    (mock data, throwaway credentials).
    """
    label = "Trust log level"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    level = (kit_vars.get("TRUST_LOG_LEVEL") or "").strip().upper()
    # Named, not "anything but DEBUG": NOTSET and a numeric level below 20 log everything too.
    if level in ("INFO", "WARNING", "ERROR", "CRITICAL"):
        return Check(label, Status.PASS, f"TRUST_LOG_LEVEL={level}")
    if not level:
        # Unset falls back to each service's own default; name it rather than assume.
        return Check(label, Status.WARN, "TRUST_LOG_LEVEL unset — set it to INFO explicitly")
    hub = os.environ.get("HUB", "prod")
    deployed = hub != ""
    return Check(
        label,
        Status.FAIL if deployed else Status.WARN,
        f"TRUST_LOG_LEVEL={level} logs trust credentials into Loki (FLIP-SR-050)"
        if level == "DEBUG"
        else f"TRUST_LOG_LEVEL={level} is not INFO, WARNING, ERROR or CRITICAL, and may log as DEBUG does",
        hints=[
            f"Edit {kit_path} → set TRUST_LOG_LEVEL=INFO, then recreate the trust APIs.",
            "A site that already ran at DEBUG should rotate both trust keys.",
            f"Judged by HUB={hub}; run with HUB= for a development kit.",
        ],
    )


def check_sample_data(kit_vars: dict[str, str], kit_present: bool, kit_path: str) -> Check:
    """TRUST_DATA_SEED (FLIP#1687): on or off, or a FAIL for a value up-trust would refuse."""
    if not kit_present:
        return Check("Sample data", Status.PENDING, "pending — needs kit file")
    try:
        seeded = trust_kit_lib.trust_data_seed(kit_vars)
    except ValueError as e:
        return Check(
            "Sample data",
            Status.FAIL,
            str(e),
            hints=[f"In {kit_path}: TRUST_DATA_SEED=false for a trust's own data, true or unset for the mock data."],
        )
    if seeded:
        return Check("Sample data", Status.PASS, "on: `make -C trust up-trust` loads the mock projects")
    return Check("Sample data", Status.PASS, "off (TRUST_DATA_SEED=false): nothing is loaded into OMOP or Orthanc")


def check_data_dir(
    label: str,
    var_name: str,
    kit_vars: dict[str, str],
    kit_present: bool,
    repo_root: Path,
    image_uid: int | None = None,
) -> Check:
    """Validate a per-trust data dir (OMOP, Orthanc) — set (or under FLIP_DATA_ROOT); exists and non-empty if real data.

    `image_uid` is the uid the dir's container runs as when the kit sets no FLIP_UID, where
    the image fixes one (deploy/portable.mk).

    A missing or empty dir is a WARN, not a FAIL: `make -C trust up-trust` creates
    the dir and seeds it from the canonical mock dataset at bring-up (FLIP#1187),
    so before the first `up` empty is the expected state. It is only wrong for a
    real hospital deployment, where the operator points these at absolute paths
    backed by real PACS / OMOP data — hence the hint. With the kit's
    TRUST_DATA_SEED=false nothing is seeded, and the hint says so instead.
    """
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    raw = kit_vars.get(var_name, "") or (data_dir_default(var_name, kit_vars) if data_root(kit_vars) else "")
    if not raw:
        # Still required: the deployed fallback is one directory for every trust on the host.
        shared = PRE_ROOT_DEFAULTS[var_name]
        return Check(
            label,
            Status.FAIL,
            f"{var_name} unset in kit file (set it, or FLIP_DATA_ROOT)",
            hints=[
                f"Set {var_name} to an absolute path of this trust's own (trust/.env.example carries the line).",
                f"On a deployed trust, unset mounts {shared}, shared by every trust on this host;"
                f" a site already using it keeps its data with {var_name}={shared}.",
            ],
        )
    # Resolve relative paths from trust/. Strip a leading "./" for a clean display.
    if raw.startswith("/"):
        resolved = Path(raw)
    else:
        resolved = repo_root / "trust" / raw.removeprefix("./")
    try:
        seeded = trust_kit_lib.trust_data_seed(kit_vars)
    except ValueError:
        seed_hint = "TRUST_DATA_SEED is neither true nor false (see Sample data): `make -C trust up-trust` refuses it."
    else:
        seed_hint = (
            "Mocked test data: `make -C trust up-trust KIT=<CODE>` creates and seeds it, nothing to do."
            if seeded
            else "Sample data is off (TRUST_DATA_SEED=false): `make -C trust up-trust` loads nothing into it."
        )
    try:
        is_dir = resolved.is_dir()
    except PermissionError:  # before Python 3.14; from 3.14 on it answers False
        is_dir = False
    if not is_dir and (closed := _untraversable(resolved)) != resolved:
        return Check(
            label,
            Status.FAIL,
            f"{resolved}: {closed} is not traversable by this user",
            hints=["Run the checklist as the user that runs the bring-up, or let that user enter the directory."],
        )
    if not is_dir:
        return Check(
            label,
            Status.WARN,
            f"{resolved} (dir does not exist yet)",
            hints=[seed_hint, f"Real data: point {var_name} at the host path holding it."],
        )
    try:
        is_empty = not any(resolved.iterdir())
    except PermissionError:
        # Dir exists but isn't readable by us — almost always because a
        # container took ownership on a previous `up` (the omop-db postgres
        # image forces PGDATA to 0700 owned by its own uid). That is the
        # normal state of a running site, so it passes when the owner is the
        # uid its container runs as: FLIP_UID, else the image's (FLIP#1731).
        # Postgres's is not fixed, so without FLIP_UID only root is wrong there
        # (a mount source Docker created, which no container can use).
        try:
            owner = resolved.stat().st_uid
        except OSError as error:
            return Check(label, Status.WARN, f"{resolved} (not readable as uid {os.getuid()}: {error.strerror})")
        flip_uid = kit_vars.get("FLIP_UID", "").strip()
        writer_uid = int(flip_uid) if flip_uid.isdigit() else image_uid
        if owner == 0:
            return Check(label, Status.WARN, f"{resolved} (owned by root, so no container can write it)")
        if writer_uid is not None and owner != writer_uid:
            return Check(
                label,
                Status.WARN,
                f"{resolved} (owned by uid {owner}, but its container runs as uid {writer_uid} and cannot write it)",
            )
        return Check(
            label,
            Status.PASS,
            f"{resolved} (in use by uid {owner}; "
            f"not readable as uid {os.getuid()}, as expected once the stack has run)",
        )
    if is_empty:
        return Check(
            label,
            Status.WARN,
            f"{resolved} (dir is empty)",
            hints=[seed_hint, f"Real data: populate {resolved} with your trust's data."],
        )
    return Check(label, Status.PASS, str(resolved))


# Every host-side bind-mount source a kit sets, with the deployed default the Makefiles use when it
# is unset (trust/Makefile IMAGES_BASE and COHORT_SNAPSHOT_DIR_HOST, trust/xnat/Makefile
# XNAT_DATA_DIR). The OMOP and Orthanc dirs have no deployed default: `check_data_dir` fails them
# unset, unless the kit sets FLIP_DATA_ROOT. On an on-prem host every one is a plain mkdir as the
# invoking user; only over a remote DOCKER_CONTEXT is the membership store prepared by a container.
DATA_DIR_DEFAULTS: dict[str, str | None] = {
    "OMOP_DATA_DIR": None,
    "ORTHANC_STORAGE_DIR": None,
    "BASE_IMAGES_DOWNLOAD_DIR": "/opt/flip/data/trust-{slot}",
    "COHORT_SNAPSHOT_STORAGE_DIR": "/opt/flip/cohort-snapshots/trust-{slot}",
    "XNAT_DATA_DIR": "/opt/flip/xnat-trust{slot}",
}
DATA_DIR_VARS: tuple[str, ...] = tuple(DATA_DIR_DEFAULTS)
# Each store's folder under the kit's FLIP_DATA_ROOT, which replaces every default above (FLIP#2113,
# deploy/data_root.mk).
DATA_ROOT_FOLDERS: dict[str, str] = {
    "OMOP_DATA_DIR": "omop",
    "ORTHANC_STORAGE_DIR": "orthanc",
    "BASE_IMAGES_DOWNLOAD_DIR": "images",
    "COHORT_SNAPSHOT_STORAGE_DIR": "cohort-snapshots",
    "XNAT_DATA_DIR": "xnat",
}


# Where each store lived before its kit had a FLIP_DATA_ROOT: the defaults above, and for OMOP and Orthanc
# what compose_trust.prod.yml mounts when they are unset.
PRE_ROOT_DEFAULTS: dict[str, str] = {
    **DATA_DIR_DEFAULTS,  # type: ignore[dict-item]
    "OMOP_DATA_DIR": "/opt/flip/omop/db_data",
    "ORTHANC_STORAGE_DIR": "/opt/flip/orthanc/orthanc-storage",
}


def _raw_data_root(kit_vars: dict[str, str]) -> str:
    """FLIP_DATA_ROOT as make reads an included kit: an inline comment dropped."""
    return kit_vars.get("FLIP_DATA_ROOT", "").split("#", 1)[0].strip()


def data_root(kit_vars: dict[str, str]) -> str:
    """The kit's FLIP_DATA_ROOT without a trailing slash; empty when unset."""
    return _raw_data_root(kit_vars).rstrip("/")


def data_root_error(kit_vars: dict[str, str]) -> str:
    """Why deploy/data_root.mk refuses the kit's FLIP_DATA_ROOT, or empty."""
    raw = _raw_data_root(kit_vars)
    if not raw:
        return ""
    if not raw.rstrip("/"):
        return "is / itself"
    if len(raw.split()) > 1:
        return f"has spaces ({raw})"
    return "" if raw.startswith("/") else f"is relative ({raw})"


def data_dir_default(var: str, kit_vars: dict[str, str]) -> str | None:
    """Where a store lives when the kit does not name it: under FLIP_DATA_ROOT, else the Makefiles' default."""
    root = data_root(kit_vars)
    return f"{root}/{DATA_ROOT_FOLDERS[var]}" if root else DATA_DIR_DEFAULTS[var]


def invoking_ids() -> tuple[int, int]:
    """The operator's uid and gid: under sudo the real user's, as the printed next step already assumes."""
    try:
        return int(os.environ.get("SUDO_UID", os.getuid())), int(os.environ.get("SUDO_GID", os.getgid()))
    except ValueError:
        return os.getuid(), os.getgid()


def prepare_host_dirs_command(kit: str) -> str:
    """The root step in full: without KIT and HUB, `make -C trust` would prepare the development paths."""
    hub = os.environ.get("HUB", "prod")
    command = f"sudo -E make -C trust prepare-host-dirs {quote_make_assignment('KIT', kit)}"
    return f"{command} {quote_make_assignment('HUB', hub)}" if hub else command


def _untraversable(path: Path) -> Path:
    """The first directory on the way to ``path`` this user cannot enter (``path`` itself when none is)."""
    return next((p for p in reversed(path.parents) if p.is_dir() and not os.access(p, os.X_OK)), path)


def _in_use(path: Path) -> bool:
    """Whether ``path`` holds something; unreadable counts, as a container owns a store it runs on."""
    try:
        return any(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError:
        return True


def _exists_or_raise(path: Path) -> bool:
    """Whether ``path`` exists, raising on a parent this user cannot traverse (EACCES).

    Not Path.exists: it re-raises EACCES before Python 3.14 and answers False from 3.14 on.
    """
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def check_data_dir_paths(kit_vars: dict[str, str], kit_present: bool, kit_path: str, kit: str = "<CODE>") -> Check:
    """Every data dir is absolute, and the bring-up can create it as this user (FLIP#1723).

    The kit template carries the development paths, which are relative: on a deployed trust they
    resolve inside this checkout, on whatever disk it sits on. An unset one falls back to its folder
    under FLIP_DATA_ROOT, else its /opt/flip default, which a non-root operator usually cannot create:
    a site installed before the membership store (FLIP#857) has no COHORT_SNAPSHOT_STORAGE_DIR, so its
    upgrade meets this.
    """
    label = "Data dir paths"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    slot = kit_vars.get("FL_KIT_SLOT_NUMBER", "").strip() or "<N>"
    if problem := data_root_error(kit_vars):
        return Check(
            label,
            Status.FAIL,
            f"FLIP_DATA_ROOT {problem}: make refuses it",
            hints=[f"Set FLIP_DATA_ROOT in {kit_path} to an absolute path on the data disk."],
        )
    relative: list[str] = []
    blocked: dict[str, str] = {}
    for var in DATA_DIR_DEFAULTS:
        default = data_dir_default(var, kit_vars)
        raw = kit_vars.get(var, "").strip()
        if raw and not raw.startswith("/"):
            relative.append(var)
            continue
        if not raw and default is None:
            continue
        path = Path(raw or default.format(slot=slot))  # type: ignore[union-attr]
        unset = "" if raw else f"unset, so {path}; "
        try:
            nearest = next(p for p in (path, *path.parents) if _exists_or_raise(p))
        except OSError as error:  # a parent this user cannot traverse
            blocked[var] = f"{unset}{_untraversable(path)} is not traversable: {error.strerror}"
            continue
        if nearest != path and not os.access(nearest, os.W_OK):
            blocked[var] = f"{unset}{nearest} is not writable"
    if kit_vars.get("FLIP_UID", "").strip() == str(invoking_ids()[0]):
        create = "create them yourself with mkdir -p: the containers run as you (FLIP_UID)."
    else:
        create = (
            "have the host owner run, once, as root (it creates them with their owners): "
            + prepare_host_dirs_command(kit)
        )
    if blocked:
        return Check(
            label,
            Status.FAIL,
            "cannot create: " + "; ".join(f"{var} ({why})" for var, why in blocked.items()),
            hints=[
                f"Either {create}",
                f"or set {', '.join(blocked)} in {kit_path} to absolute paths this user can create.",
            ],
        )
    if relative:
        return Check(
            label,
            Status.WARN,
            f"relative, so inside this checkout: {', '.join(relative)}",
            hints=[
                f"Set {', '.join(relative)} in {kit_path} to absolute paths on the data disk, then {create}",
                "On a site that already holds data this moves the data too: a planned step with the stack stopped,",
                "never part of an upgrade.",
            ],
        )
    return Check(label, Status.PASS, "absolute")


def check_data_root(kit_vars: dict[str, str], kit_present: bool, kit_file: Path, kit_path: str) -> Check:
    """FLIP_DATA_ROOT moves no data and is one trust's (FLIP#2113).

    FAIL when it would start a store that holds data at its old default on an empty folder under the root
    (a root added to a running site), or when another kit beside this one names the same root.
    """
    label = "Data root"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    root = data_root(kit_vars)
    if not root:
        return Check(label, Status.PASS, "unset: each store keeps its own default")
    if data_root_error(kit_vars):
        return Check(label, Status.FAIL, "malformed: see Data dir paths")
    shared = sorted(
        other.name
        for other in kit_file.parent.glob(".env.*")
        if other != kit_file and not other.name.endswith(".example") and data_root(read_kit_vars(other)) == root
    )
    if shared:
        return Check(
            label,
            Status.FAIL,
            f"{root} is also the root of {', '.join(shared)}: the trusts would share their databases and archives",
            hints=[f"Give {kit_path} its own FLIP_DATA_ROOT, e.g. /opt/flip/<CODE>-<hub>."],
        )
    slot = kit_vars.get("FL_KIT_SLOT_NUMBER", "").strip() or "<N>"
    moved = [
        f"{var} ({old} → {Path(root) / folder})"
        for var, folder in DATA_ROOT_FOLDERS.items()
        if not kit_vars.get(var, "").strip()
        and _in_use(old := Path(PRE_ROOT_DEFAULTS[var].format(slot=slot)))
        and not _in_use(Path(root) / folder)
    ]
    if moved:
        return Check(
            label,
            Status.FAIL,
            "would start on empty folders, leaving data behind: " + "; ".join(moved),
            hints=[
                f"FLIP_DATA_ROOT moves no data. Set each of these in {kit_path} to where it is now,",
                "or move the data under the root with the stack stopped, never as part of an upgrade.",
            ],
        )
    return Check(label, Status.PASS, root)


IMAGE_USERS = (
    "1000 imaging-api, data-access-api and the FLARE client, 999 Orthanc, 1001 xnat-web, 49999 the Flower SuperNode"
)


def check_container_uid(kit_vars: dict[str, str], kit_present: bool, kit_path: str, kit: str = "<CODE>") -> Check:
    """Who the bind-mount writers run as, and whether that is this user (FLIP#1731).

    FLIP_UID/FLIP_GID set to the operator's own ids means the directories they create need no
    chown; any other value, or none, means `prepare-host-dirs` as root (or a root bring-up).
    FLIP_GID follows FLIP_UID when the kit sets only the uid, as deploy/portable.mk does.
    """
    label = "Container uid"
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    uid = kit_vars.get("FLIP_UID", "").strip()
    gid = kit_vars.get("FLIP_GID", "").strip() or uid
    root_step = (
        "running as root: the bring-up sets the owners itself"
        if os.getuid() == 0
        else f"the bind-mount dirs need `{prepare_host_dirs_command(kit)}`"
    )
    if not uid and not gid:
        return Check(label, Status.PASS, f"unset: each image's own user ({IMAGE_USERS}); {root_step}")
    if not (uid.isdigit() and gid.isdigit()):
        return Check(
            label,
            Status.FAIL,
            f"FLIP_UID={uid or '<unset>'} FLIP_GID={gid or '<unset>'}: FLIP_UID must be set, as a number"
            " (FLIP_GID defaults to it)",
            hints=[f"Edit {kit_path}: FLIP_UID=$(id -u) and FLIP_GID=$(id -g) run the containers as you."],
        )
    me = invoking_ids()
    if (int(uid), int(gid)) == me:
        return Check(label, Status.PASS, f"{uid}:{gid}, this user: directories you create need no chown")
    return Check(label, Status.WARN, f"{uid}:{gid} is not this user ({me[0]}:{me[1]}); {root_step}")


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def kit_file_label(kit_file: Path, repo_root: Path) -> str:
    """Show paths inside this checkout relative to its root; preserve external paths."""
    try:
        return str(kit_file.relative_to(repo_root))
    except ValueError:
        return str(kit_file)


def run_checks(kit: str, repo_root: Path, kit_file: Path | None = None) -> list[Check]:
    kit_file = kit_file or repo_root / "trust" / f".env.{kit}"
    kit_path = kit_file_label(kit_file, repo_root)
    kit_vars = read_kit_vars(kit_file)
    kit_present = kit_file.is_file()
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")

    return [
        check_swarm(),
        check_kit_file(kit, kit_file, kit_path),
        check_hub_shared(kit_vars, kit_present),
        check_hub_shared_current(kit_vars, kit_present),
        check_hub_reachable(kit_vars, kit_present),
        check_fl_endpoint_reachable(kit_vars, kit_present),
        check_registries_reachable(),
        check_kit_credentials(kit_vars, kit_present, kit, kit_path),
        check_expected_trust_id_self_check(kit_vars, kit_present, kit, kit_path),
        check_fl_kit_dir_set(kit_vars, kit_present, kit, kit_path),
        check_fl_kit_dir_exists(fl_kit_dir, kit_present),
        check_fl_kit_contents(kit_vars, kit_present),
        check_gpu_capacity(kit_vars, kit_present, kit, kit_path),
        check_site_privacy_policy(kit_vars, kit_present, kit, repo_root, kit_path),
        check_governance_document(kit_vars, kit_present, kit, repo_root, kit_file),
        check_unrotated_passwords(kit_vars, kit_present, repo_root, kit, kit_path),
        check_log_level(kit_vars, kit_present, kit_path),
        check_sample_data(kit_vars, kit_present, kit_path),
        check_data_dir("OMOP data dir", "OMOP_DATA_DIR", kit_vars, kit_present, repo_root),
        check_data_dir("Orthanc storage dir", "ORTHANC_STORAGE_DIR", kit_vars, kit_present, repo_root, image_uid=999),
        check_data_dir_paths(kit_vars, kit_present, kit_path, kit),
        check_data_root(kit_vars, kit_present, kit_file, kit_path),
        check_container_uid(kit_vars, kit_present, kit_path, kit),
    ]


def write_report(checks: list[Check], out: Path, kit: str, started: datetime) -> None:
    """The checklist as a report file. A folder that cannot be written is a note, never a failed checklist."""
    form = "%Y-%m-%dT%H:%M:%SZ"
    try:
        path = trust_checks.write_report(
            checks,
            out,
            stem=f"checklist-{kit}-{started:%Y%m%dT%H%M%SZ}",
            latest=f"latest-checklist-{kit}",
            title=f"Onboarding checklist: {kit}",
            started=started.strftime(form),
            finished=datetime.now(timezone.utc).strftime(form),
            shape="compose",
            kit=kit,
        )
    except OSError as error:
        print(f"\n  Report not written to {out}: {error}")
        return
    print(f"\n  Report: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "kit",
        nargs="?",
        default=None,
        help="Slot name (e.g. Trust_2). Defaults to Trust_2 — the conventional on-prem slot.",
    )
    parser.add_argument(
        "--kit-file",
        type=Path,
        help="Kit file selected by Make. Relative paths resolve from the repo root; defaults to trust/.env.<KIT>.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Running as the gate of a make verb (up-onprem-trust, upgrade-onprem-trust): on READY, "
        "let it continue rather than suggest a command.",
    )
    parser.add_argument("--out", type=Path, help="Also write the checklist as a JSON and markdown report here.")
    args = parser.parse_args()

    # The governance and site-privacy checks run stdlib tomllib under this interpreter.
    if sys.version_info < (3, 11):
        sys.exit(
            f"❌ This checklist needs Python 3.11 or later; this is {sys.version.split()[0]}.\n"
            "   `make onboard-onprem-trust` runs it under uv, which installs one if it can download it;\n"
            "   otherwise run it with a python3.11+ the host provides."
        )

    kit = args.kit or "Trust_2"

    repo_root = Path(__file__).resolve().parents[3]
    kit_file = args.kit_file or Path("trust") / f".env.{kit}"
    if not kit_file.is_absolute():
        kit_file = repo_root / kit_file

    print()
    heading(f"On-prem trust onboarding checklist — kit {kit_file_label(kit_file, repo_root)}")

    print()
    print(f"  {BOLD}Checks:{RESET}")
    print()

    started = datetime.now(timezone.utc)
    try:
        checks = run_checks(kit, repo_root, kit_file)
    except Exception as error:  # noqa: BLE001 — a checklist that broke says nothing about the host
        checks = [trust_checks.could_not_run("Checklist", error)]
    label_width = max(len(c.name) + 1 for c in checks)  # +1 for the trailing ":"
    for c in checks:
        render_check(c, label_width)
    if args.out:
        write_report(checks, args.out, kit, started)

    n_pass = sum(1 for c in checks if c.status == Status.PASS)
    n_fail = sum(1 for c in checks if c.status == Status.FAIL)
    n_pending = sum(1 for c in checks if c.status == Status.PENDING)
    n_warn = sum(1 for c in checks if c.status == Status.WARN)
    # Warnings are advisory — they don't block readiness, but they DO count
    # toward the summary so the operator can see them in the headline.
    is_ready = trust_checks.exit_code(checks) == 0

    print()
    if is_ready:
        suffix = f", {n_warn} warning{'s' if n_warn != 1 else ''}" if n_warn else ""
        heading(f"Status: READY {Status.PASS.glyph}  ({n_pass}/{len(checks)} pass{suffix})")
        print()
        if args.gate:
            # The verb that ran this gate carries on; naming a command here would name the wrong
            # one for the upgrade (up-onprem-trust resets XNAT) or the one already running.
            print("  Proceeding.")
        else:
            print("  Bring the stack up (first install):")
            # Docker socket access is all the stack needs. Only a checklist that itself ran under
            # sudo (the playbook-provisioned host, whose login user has no socket access) prints the
            # same prefix for the next step (FLIP#1723).
            prefix = "sudo -E " if os.environ.get("SUDO_USER") else ""
            command = (
                f"{prefix}make up-onprem-trust {quote_make_assignment('KIT', kit)} "
                f"{quote_make_assignment('KIT_FILE', kit_file)}"
            )
            upgrade = f"{prefix}make upgrade-onprem-trust {quote_make_assignment('KIT', kit)}"
            hub = os.environ.get("HUB", "prod")
            if hub and hub != "prod":
                command += f" {quote_make_assignment('HUB', hub)}"
                upgrade += f" {quote_make_assignment('HUB', hub)}"
            print(f"      {BOLD}{command}{RESET}")
            # The checklist is also step one of a site upgrade, where the first-install verb would
            # reset XNAT's archive and database.
            print("  A site that is already running upgrades instead (the command above would reset XNAT):")
            print(f"      {BOLD}{upgrade}{RESET}")
        if n_warn:
            print(
                f"  {YELLOW}Heads-up:{RESET} review the {YELLOW}⚠️{RESET}  warning(s) above "
                "before running in production."
            )
        print()
        sys.exit(0)
    parts = [f"{n_pass} pass", f"{n_fail} fail", f"{n_pending} pending"]
    if n_warn:
        parts.append(f"{n_warn} warn")
    n_error = sum(1 for c in checks if c.status == Status.ERROR)
    if n_error:
        parts.append(f"{n_error} could not run")
    heading(f"Status: NOT READY  ({', '.join(parts)})")
    if n_error:
        print(f"  The checklist itself broke ({Status.ERROR.glyph} above): that says nothing about this host yet.")
    if n_fail or n_pending:
        print(f"  Fix the {RED}❌{RESET} items and resolve the {YELLOW}⏳{RESET} pending steps above.")
    print()
    sys.exit(trust_checks.exit_code(checks))


if __name__ == "__main__":
    main()
