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
    if not any(resolved.iterdir()):
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
        check_fl_kit_dir_set(kit_vars, kit_present, kit),
        check_fl_kit_dir_exists(fl_kit_dir, kit_present),
        check_fl_kit_contents(kit_vars, kit_present),
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
    all_pass = n_fail == 0 and n_pending == 0

    print()
    if all_pass:
        heading(f"Status: READY {Status.PASS.glyph}  ({n_pass}/{len(checks)} checks passed)")
        print()
        print(f"  Bring the stack up:")
        print(f"      {BOLD}make up-onprem-trust KIT={kit}{RESET}")
        print()
        sys.exit(0)
    summary = f"Status: NOT READY  ({n_pass} pass, {n_fail} fail, {n_pending} pending)"
    heading(summary)
    print(f"  Fix the {RED}❌{RESET} items and resolve the {YELLOW}⏳{RESET} pending steps above.")
    print()
    sys.exit(1)


if __name__ == "__main__":
    main()
