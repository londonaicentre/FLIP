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
"""On-prem trust readiness checklist.

Diagnoses what's still missing in the operator's setup before `make
up-onprem-trust KIT=<slot>` will succeed: public IP, docker swarm state,
kit file presence, Hub-shared block, Kit credentials, FL_KIT_DIR + its
on-disk contents, OMOP / Orthanc data dirs.

Each check renders ✅ (pass), ❌ (fail with concrete fix hint), or ⏳
(pending — depends on something earlier that hasn't been satisfied yet,
typically the kit file). Runs every check even when the kit file is
absent so a first-time operator can run `make onboard-onprem-trust`
straight after cloning the repo and see what they need to ask the FLIP
admin for.

Exits 0 when every check passes, 1 otherwise. The root Makefile's
`up-onprem-trust` target wraps this script as a precheck so an operator
gets diagnostics instead of a cryptic compose / pydantic failure deeper
in the stack.

Usage:
    uv run scripts/onboard_onprem_trust.py [KIT]
    # KIT defaults to Trust_2 (the conventional on-prem slot).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

WIDTH = 71

# Hub-shared keys — MUST stay in lockstep with HUB_SHARED_KEYS in
# scripts/sync_trust_kit.py and HUB_SHARED_ENV_KEYS in
# flip_api/scripts/register_trust.py.
HUB_SHARED_KEYS: tuple[str, ...] = (
    "AES_KEY_BASE64", "CENTRAL_HUB_API_URL", "TRUST_API_KEY_HEADER", "FL_BACKEND",
    "FLOWER_KIT_DATE", "FLARE_KIT_DATE", "DOCKER_TAG", "DOCKER_REGISTRY",
    "DOCKER_FL_TAG", "DOCKER_FL_REGISTRY", "NLB_SUBDOMAIN", "FL_SERVER_PORT",
)
KIT_CRED_KEYS: tuple[str, ...] = (
    "TRUST_API_KEY", "TRUST_INTERNAL_SERVICE_KEY", "FL_KIT_SLOT",
    "FL_KIT_SLOT_NUMBER", "EXPECTED_TRUST_ID",
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
    "XNAT_ADMIN_INITIAL_PASSWORD",
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
RESET  = "\033[0m"  if _TTY else ""
BOLD   = "\033[1m"  if _TTY else ""
DIM    = "\033[2m"  if _TTY else ""
GREEN  = "\033[32m" if _TTY else ""
RED    = "\033[31m" if _TTY else ""
YELLOW = "\033[33m" if _TTY else ""
CYAN   = "\033[36m" if _TTY else ""


class Status(Enum):
    PASS = ("✅", GREEN)
    FAIL = ("❌", RED)
    PENDING = ("⏳", YELLOW)
    WARN = ("⚠️ ", YELLOW)  # trailing space — ⚠️ renders narrow on some terminals

    @property
    def glyph(self) -> str:
        return self.value[0]

    @property
    def colour(self) -> str:
        return self.value[1]


@dataclass
class Check:
    """One row of the checklist."""

    label: str
    status: Status
    detail: str = ""
    hints: list[str] = field(default_factory=list)


def rule(char: str = "═") -> None:
    print(char * WIDTH)


def heading(text: str) -> None:
    rule()
    print(f"  {BOLD}{text}{RESET}")
    rule()


def render_check(c: Check, label_width: int) -> None:
    """Render one check row + any indented hints."""
    label = f"{c.label}:".ljust(label_width)
    print(f"  {c.status.colour}{c.status.glyph}{RESET} {label} {c.detail}")
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


def fetch_public_ip(timeout: float = 5.0) -> str | None:
    """Best-effort fetch of this host's public IPv4 via ipify. Returns None on failure."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as response:  # noqa: S310
            return response.read().decode("ascii").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def docker_swarm_state() -> str:
    """Return the local docker swarm state (`active`, `inactive`, etc.) or `unavailable`."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip() or "inactive"


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
            capture_output=True, text=True, timeout=5,
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
    if state == "unavailable":
        return Check(
            "Docker swarm", Status.FAIL,
            "docker not reachable (daemon down or not installed)",
            hints=["Install Docker, start the daemon, then run: docker swarm init"],
        )
    return Check(
        "Docker swarm", Status.FAIL,
        f"{state} (overlay networks require swarm mode)",
        hints=["One-off host setup: docker swarm init"],
    )


def check_kit_file(kit: str, kit_file: Path) -> Check:
    if kit_file.is_file():
        return Check("Kit file present", Status.PASS, f"trust/.env.{kit}")
    return Check(
        "Kit file MISSING", Status.FAIL, f"trust/.env.{kit}",
        hints=[
            f"Ask the FLIP admin to package + send your kit (`make package-onprem-trust-kit",
            f"  KIT={kit}` from deploy/providers/AWS), extract the tarball, then:",
            f"    cp <extracted-dir>/.env.{kit} trust/.env.{kit}",
        ],
    )


def check_hub_shared(kit_vars: dict[str, str], kit_present: bool) -> Check:
    if not kit_present:
        return Check("Hub-shared block (12 keys)", Status.PENDING, "pending — needs kit file")
    missing = [k for k in HUB_SHARED_KEYS if not is_filled(kit_vars.get(k))]
    if not missing:
        hub_url = kit_vars.get("CENTRAL_HUB_API_URL", "").removesuffix("/api")
        return Check(
            "Hub-shared block (12 keys)", Status.PASS, "populated",
            hints=[f"UI URL: {hub_url or '<unset>'}"],
        )
    return Check(
        "Hub-shared block (12 keys)", Status.FAIL,
        f"{len(missing)} unfilled: {', '.join(missing)}",
        hints=["Ask the FLIP admin to run 'make sync-trust-kit KIT=<slot>' and re-send the kit."],
    )


def check_kit_credentials(kit_vars: dict[str, str], kit_present: bool, kit: str) -> Check:
    if not kit_present:
        return Check("Kit credentials (5 keys)", Status.PENDING, "pending — needs kit file")
    missing = [k for k in KIT_CRED_KEYS if not is_filled(kit_vars.get(k))]
    if not missing:
        return Check("Kit credentials (5 keys)", Status.PASS, "populated")
    return Check(
        "Kit credentials (5 keys)", Status.FAIL,
        f"{len(missing)} unfilled: {', '.join(missing)}",
        hints=[
            "Ask the FLIP admin to UI-register your trust (Add Trust modal),",
            f"  paste the 5 lines into trust/.env.{kit}, and re-send the kit.",
        ],
    )


def check_expected_trust_id_self_check(kit_vars: dict[str, str], kit_present: bool, kit: str) -> Check:
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
    if not kit_present:
        return Check("EXPECTED_TRUST_ID self-check", Status.PENDING, "pending — needs kit file")
    raw = kit_vars.get("EXPECTED_TRUST_ID", "")
    if not is_filled(raw):
        return Check(
            "EXPECTED_TRUST_ID self-check", Status.FAIL,
            "unset — wrong-host safety check disabled",
            hints=[
                "Without EXPECTED_TRUST_ID, a kit deployed to the wrong host will",
                "  silently act as the wrong trust until something downstream breaks.",
                f"Re-register on the hub side and re-send the kit, OR ask the admin to",
                f"  fill the value into trust/.env.{kit} before bringing the stack up.",
            ],
        )
    return Check("EXPECTED_TRUST_ID self-check", Status.PASS, f"set to {raw}")


def check_fl_kit_dir_set(kit_vars: dict[str, str], kit_present: bool, kit: str) -> Check:
    if not kit_present:
        return Check("FL_KIT_DIR set", Status.PENDING, "pending — needs kit file")
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")
    if fl_kit_dir:
        return Check("FL_KIT_DIR set", Status.PASS, fl_kit_dir)
    return Check(
        "FL_KIT_DIR set", Status.FAIL, "not set in kit file",
        hints=[f"Add FL_KIT_DIR=<absolute path> to trust/.env.{kit}"],
    )


def check_fl_kit_dir_exists(fl_kit_dir: str, kit_present: bool) -> Check:
    if not kit_present:
        return Check("FL_KIT_DIR on disk", Status.PENDING, "pending — needs kit file")
    if not fl_kit_dir:
        return Check("FL_KIT_DIR on disk", Status.PENDING, "pending — FL_KIT_DIR unset")
    if Path(fl_kit_dir).is_dir():
        return Check("FL_KIT_DIR on disk", Status.PASS, fl_kit_dir)
    return Check(
        "FL_KIT_DIR on disk", Status.FAIL, f"{fl_kit_dir} (does not exist)",
        hints=[
            "Extract the FL kit tarball from the FLIP admin at this path,",
            "  preserving the net-1/ hierarchy.",
        ],
    )


def check_fl_kit_contents(kit_vars: dict[str, str], kit_present: bool) -> Check:
    if not kit_present:
        return Check("FL kit contents", Status.PENDING, "pending — needs kit file")
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")
    backend = kit_vars.get("FL_BACKEND", "")
    slot = kit_vars.get("FL_KIT_SLOT", "")
    slot_number = kit_vars.get("FL_KIT_SLOT_NUMBER", "")
    if not (fl_kit_dir and backend and slot):
        return Check(
            "FL kit contents", Status.PENDING,
            "pending — FL_KIT_DIR / FL_BACKEND / FL_KIT_SLOT not all set in kit file",
        )
    if not Path(fl_kit_dir).is_dir():
        return Check(
            "FL kit contents", Status.PENDING,
            f"pending — waiting on FL_KIT_DIR ({fl_kit_dir}) to exist on disk",
        )
    root = Path(fl_kit_dir)
    if backend == "nvflare":
        target = root / "net-1" / "services" / slot
        sub = {p: (target / p).is_dir() for p in ("local", "startup", "transfer")}
        if all(sub.values()):
            return Check(
                "FL kit contents (nvflare)", Status.PASS,
                f"{target}/{{local,startup,transfer}}",
            )
        missing = ", ".join(p for p, ok in sub.items() if not ok)
        return Check(
            "FL kit contents (nvflare)", Status.FAIL, f"missing {missing} under {target}",
            hints=["Check the tarball was extracted preserving the net-1/services/<slot>/ hierarchy."],
        )
    # flower
    certs = root / "net-1" / "certificates"
    creds = root / "net-1" / "keys" / f"supernode_credentials_{slot_number}"
    if certs.is_dir() and creds.is_file():
        return Check(
            "FL kit contents (flower)", Status.PASS,
            f"{certs}/ + supernode_credentials_{slot_number}",
        )
    missing_parts = []
    if not certs.is_dir():
        missing_parts.append(f"{certs}/")
    if not creds.is_file():
        missing_parts.append(str(creds))
    return Check(
        "FL kit contents (flower)", Status.FAIL, f"missing: {' '.join(missing_parts)}",
        hints=["Check the tarball was extracted preserving the net-1/{certificates,keys}/ hierarchy."],
    )


def check_gpu_capacity(kit_vars: dict[str, str], kit_present: bool, kit: str) -> Check:
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
    if not kit_present:
        return Check("fl-client GPU capacity", Status.PENDING, "pending — needs kit file")
    raw = (kit_vars.get("NUM_AVAILABLE_GPUS") or "").strip()
    if not raw:
        return Check(
            "fl-client GPU capacity", Status.PASS,
            "NUM_AVAILABLE_GPUS unset in kit (template treats as 0 → CPU-only)",
        )
    try:
        kit_gpus = int(raw)
    except ValueError:
        return Check(
            "fl-client GPU capacity", Status.FAIL,
            f"NUM_AVAILABLE_GPUS='{raw}' is not an integer",
            hints=[f"Edit trust/.env.{kit} → Trust-local credentials section."],
        )
    if kit_gpus <= 0:
        return Check(
            "fl-client GPU capacity", Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus} (CPU-only)",
        )
    host_gpus = detect_host_gpu_count()
    if host_gpus is None:
        return Check(
            "fl-client GPU capacity", Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus}; host GPU count undetectable (nvidia-smi errored)",
        )
    if host_gpus >= kit_gpus:
        return Check(
            "fl-client GPU capacity", Status.PASS,
            f"NUM_AVAILABLE_GPUS={kit_gpus} ≤ host NVIDIA GPUs ({host_gpus})",
        )
    return Check(
        "fl-client GPU capacity", Status.WARN,
        f"NUM_AVAILABLE_GPUS={kit_gpus} but host exposes {host_gpus} NVIDIA GPU(s)",
        hints=[
            "fl-client will crash-loop on `num_of_gpus specified exceeds available GPUs`.",
            f"Edit trust/.env.{kit} → set NUM_AVAILABLE_GPUS=0 and MEMORY_PER_GPU_IN_GIB=0",
            "  for CPU-only (slow but functional), or move to a host with the expected GPU(s).",
        ],
    )


def check_unrotated_passwords(
    kit_vars: dict[str, str], kit_present: bool, repo_root: Path, kit: str,
) -> Check:
    """Soft-warn if any Trust-local password still matches the .example template.

    The packager hands the operator a kit whose Trust-local credentials section
    is verbatim from the .production.example (dev-friendly defaults). For a
    real on-prem hospital deployment the operator must rotate these before
    bringing the stack up. Returns Status.WARN (not FAIL) — defaults are
    technically usable for dev/testing and we don't want to block that path.
    """
    if not kit_present:
        return Check("Trust-local passwords", Status.PENDING, "pending — needs kit file")
    # Use FL_KIT_SLOT (the canonical slot name minted by the hub) to find the
    # template — not the KIT argument. They CAN differ: the coexistence flow
    # uses `cp .env.Trust_2.production.example .env.Trust_2_prod`, leaving the
    # kit file named Trust_2_prod but its FL_KIT_SLOT still set to Trust_2.
    # Without this fall-through the check would silently skip on every
    # custom-named kit. Falls back to KIT when the slot isn't filled in yet
    # (the kit-credentials check below will already be failing in that case).
    canonical = kit_vars.get("FL_KIT_SLOT") or kit
    if not canonical or "<run-make-" in canonical:
        canonical = kit
    # Prefer the production template (closer to what an on-prem operator copied);
    # fall back to the dev template; skip silently if neither exists.
    candidates = [
        repo_root / "trust" / f".env.{canonical}.production.example",
        repo_root / "trust" / f".env.{canonical}.example",
    ]
    template = next((c for c in candidates if c.is_file()), None)
    if template is None:
        names = " or ".join(c.name for c in candidates)
        return Check(
            "Trust-local passwords", Status.PASS,
            f"skipped — no template found ({names})",
        )
    template_vars = read_kit_vars(template)
    unchanged = [
        key for key in TRUST_LOCAL_PASSWORD_KEYS
        if kit_vars.get(key) and kit_vars[key] == template_vars.get(key)
    ]
    if not unchanged:
        return Check("Trust-local passwords", Status.PASS, "all rotated from template defaults")
    return Check(
        "Trust-local passwords", Status.WARN,
        f"{len(unchanged)}/{len(TRUST_LOCAL_PASSWORD_KEYS)} still match {template.name} defaults",
        hints=[
            f"Unchanged: {', '.join(unchanged)}",
            f"For a real on-prem deployment, edit trust/.env.{kit} → Trust-local credentials",
            "  section and replace with production-grade secrets.",
        ],
    )


def check_data_dir(
    label: str,
    var_name: str,
    update_target: str,
    kit_vars: dict[str, str],
    kit_present: bool,
    repo_root: Path,
) -> Check:
    """Validate a per-trust data dir (OMOP, Orthanc) — set, exists, non-empty.

    Mocked test data lives under trust/ — when ORTHANC_STORAGE_DIR / OMOP_DATA_DIR
    is a relative path it resolves from trust/ and is populated by the
    `make -C trust update-{orthanc,omop}-data` targets. For a real hospital
    deployment the operator would point these at absolute paths backed by real
    PACS / OMOP data; the check then flags an empty mount, with the right
    "point at real data" hint instead.
    """
    if not kit_present:
        return Check(label, Status.PENDING, "pending — needs kit file")
    raw = kit_vars.get(var_name, "")
    if not raw:
        return Check(label, Status.FAIL, f"{var_name} unset in kit file")
    # Resolve relative paths from trust/. Strip a leading "./" for a clean display.
    if raw.startswith("/"):
        resolved = Path(raw)
    else:
        resolved = repo_root / "trust" / raw.removeprefix("./")
    if not resolved.is_dir():
        return Check(
            label, Status.FAIL, f"{resolved} (dir does not exist)",
            hints=[
                f"If using the mocked test data, run: make -C trust {update_target}",
                f"Otherwise point {var_name} at the host path holding your real data.",
            ],
        )
    try:
        is_empty = not any(resolved.iterdir())
    except PermissionError:
        # Dir exists but isn't readable by us — almost always because a
        # container took ownership on a previous `up` (the omop-db postgres
        # image forces PGDATA to 0700 owned by its own uid). It's therefore
        # in use and populated; we just can't introspect it, so don't block
        # a re-run of onboarding after the stack has come up.
        try:
            owner = f"uid {resolved.stat().st_uid}"
        except OSError:
            owner = "another user"
        return Check(
            label, Status.WARN,
            f"{resolved} (owned by {owner}, not readable as uid {os.getuid()} "
            f"— assumed populated; expected after the stack has run once)",
        )
    if is_empty:
        return Check(
            label, Status.FAIL, f"{resolved} (dir is empty)",
            hints=[
                f"If using the mocked test data, run: make -C trust {update_target}",
                f"Otherwise populate {resolved} with your trust's real data.",
            ],
        )
    return Check(label, Status.PASS, str(resolved))


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def run_checks(kit: str, repo_root: Path) -> list[Check]:
    kit_file = repo_root / "trust" / f".env.{kit}"
    kit_vars = read_kit_vars(kit_file)
    kit_present = kit_file.is_file()
    fl_kit_dir = kit_vars.get("FL_KIT_DIR", "")

    return [
        check_swarm(),
        check_kit_file(kit, kit_file),
        check_hub_shared(kit_vars, kit_present),
        check_kit_credentials(kit_vars, kit_present, kit),
        check_expected_trust_id_self_check(kit_vars, kit_present, kit),
        check_fl_kit_dir_set(kit_vars, kit_present, kit),
        check_fl_kit_dir_exists(fl_kit_dir, kit_present),
        check_fl_kit_contents(kit_vars, kit_present),
        check_gpu_capacity(kit_vars, kit_present, kit),
        check_unrotated_passwords(kit_vars, kit_present, repo_root, kit),
        check_data_dir(
            "OMOP data dir", "OMOP_DATA_DIR", "update-omop-data",
            kit_vars, kit_present, repo_root,
        ),
        check_data_dir(
            "Orthanc storage dir", "ORTHANC_STORAGE_DIR", "update-orthanc-data",
            kit_vars, kit_present, repo_root,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "kit", nargs="?", default=None,
        help="Slot name (e.g. Trust_2). Defaults to Trust_2 — the conventional on-prem slot.",
    )
    args = parser.parse_args()

    kit = args.kit or "Trust_2"
    kit_defaulted = args.kit is None

    repo_root = Path(__file__).resolve().parent.parent

    print()
    heading(f"On-prem trust onboarding checklist — kit trust/.env.{kit}")
    if kit_defaulted:
        print(f"  {DIM}(KIT defaulted to Trust_2 — override with: "
              f"make onboard-onprem-trust KIT=<slot>){RESET}")

    print()
    ip = fetch_public_ip()
    ip_display = ip or f"{DIM}<could not detect — set it manually>{RESET}"
    print(f"  {BOLD}Your public IP:{RESET}  {CYAN}{ip_display}{RESET}")
    print(f"  Send this to the FLIP admin so they can open the prod FL-server NLB")
    print(f"  (the admin runs this from {BOLD}deploy/providers/AWS{RESET}, with prod AWS creds):")
    print(f"      cd deploy/providers/AWS")
    print(f"      AWS_PROFILE=prod make allow-local-trust-nlb "
          f"LOCAL_TRUST_IP={ip or '<your-ip>'} PROD=true")
    print()
    print(f"  {BOLD}Checks:{RESET}")
    print()

    checks = run_checks(kit, repo_root)
    label_width = max(len(c.label) + 1 for c in checks)  # +1 for the trailing ":"
    for c in checks:
        render_check(c, label_width)

    n_pass = sum(1 for c in checks if c.status == Status.PASS)
    n_fail = sum(1 for c in checks if c.status == Status.FAIL)
    n_pending = sum(1 for c in checks if c.status == Status.PENDING)
    n_warn = sum(1 for c in checks if c.status == Status.WARN)
    # Warnings are advisory — they don't block readiness, but they DO count
    # toward the summary so the operator can see them in the headline.
    is_ready = n_fail == 0 and n_pending == 0

    print()
    if is_ready:
        suffix = f", {n_warn} warning{'s' if n_warn != 1 else ''}" if n_warn else ""
        heading(f"Status: READY {Status.PASS.glyph}  ({n_pass}/{len(checks)} pass{suffix})")
        print()
        print(f"  Bring the stack up:")
        print(f"      {BOLD}make up-onprem-trust KIT={kit}{RESET}")
        if n_warn:
            print(f"  {YELLOW}Heads-up:{RESET} review the {YELLOW}⚠️{RESET}  warning(s) above before running in production.")
        print()
        sys.exit(0)
    parts = [f"{n_pass} pass", f"{n_fail} fail", f"{n_pending} pending"]
    if n_warn:
        parts.append(f"{n_warn} warn")
    heading(f"Status: NOT READY  ({', '.join(parts)})")
    print(f"  Fix the {RED}❌{RESET} items and resolve the {YELLOW}⏳{RESET} pending steps above.")
    print()
    sys.exit(1)


if __name__ == "__main__":
    main()
