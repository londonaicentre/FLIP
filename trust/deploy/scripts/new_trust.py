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
"""Scaffold a new trust kit file from the base template (stdlib only).

Produces ``trust/.env.<CODE>.<env>`` by copying the base template (host-local
profile + ``<run-make-register-trust>`` placeholders for the hub-managed blocks),
minting a random value for every Trust-local password the template ships as a
mock-stack default, and prepending the trust's identity (``TRUST_NAME`` /
``TRUST_CODE`` / ``TRUST_REGION``).

The result is ready for registration to fill in the credentials, FL kit slot and
hub-shared block: ``make register-trust KIT=<CODE>`` on the dev hub,
``make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<env>`` on a deployed one. Refuses to overwrite an
existing kit. Driven by ``make new-trust``.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

# Trust-local passwords minted per kit, so no site ever runs the template's mock-stack
# defaults. Must stay in lockstep with TRUST_LOCAL_PASSWORD_KEYS in onboard_onprem_trust.py,
# whose readiness checklist refuses a stag/prod kit that still carries a template value.
# XNAT_ADMIN_INITIAL_PASSWORD is deliberately absent: it is XNAT's factory password, which
# xnat-init replaces with XNAT_ADMIN_PASSWORD.
TRUST_LOCAL_PASSWORD_KEYS: tuple[str, ...] = (
    "ORTHANC_PASSWORD",
    "OMOP_POSTGRES_PASSWORD",
    "DATA_ACCESS_POSTGRES_PASSWORD",
    "XNAT_ADMIN_PASSWORD",
    "XNAT_SERVICE_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)


def mint_passwords(template: str) -> str:
    """Replace each Trust-local password assignment in the template with a random value."""
    lines = []
    for line in template.splitlines():
        key, sep, _ = line.partition("=")
        if sep and key in TRUST_LOCAL_PASSWORD_KEYS:
            line = f"{key}={secrets.token_urlsafe(24)}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def scaffold(target: Path, template: Path, name: str, code: str, region: str | None = None) -> None:
    """Create the kit file: the trust's identity, then the template with its passwords minted.

    Raises:
        FileExistsError: If ``target`` already exists.
    """
    identity = [
        "# ── Trust identity (sent to the hub by `make register-trust`) ──",
        f"TRUST_NAME={name}",
        f"TRUST_CODE={code}",
    ]
    if region:
        identity.append(f"TRUST_REGION={region}")
    identity.append("")  # blank line before the template body

    # Created 0600 before any content lands, since the file holds credentials from its first byte.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as kit:
        kit.write("\n".join(identity) + "\n" + mint_passwords(template.read_text()))


def main() -> None:
    """CLI entry point: scaffold trust/.env.<CODE>.<env> from the base template."""
    parser = argparse.ArgumentParser(description="Scaffold a new trust kit file.")
    parser.add_argument("--code", required=True, help="Trust short code — the kit-file handle (e.g. KCH).")
    parser.add_argument("--name", required=True, help="Trust display name registered on the hub.")
    parser.add_argument("--region", default=None, help="Optional NHS region.")
    parser.add_argument(
        "--env",
        required=True,
        choices=("development", "stag", "prod"),
        help="Target env — the kit-file suffix (deploy/env_mode.mk's ENV).",
    )
    parser.add_argument("--template", default=None, help="Base template (default trust/.env.example).")
    parser.add_argument("--trust-dir", default=None, help="Override the trust/ directory (testing).")
    args = parser.parse_args()

    trust_dir = Path(args.trust_dir) if args.trust_dir else Path(__file__).resolve().parents[3] / "trust"
    target = trust_dir / f".env.{args.code}.{args.env}"
    if target.exists():
        print(f"❌ {target} already exists — refusing to overwrite (delete it first).", file=sys.stderr)
        sys.exit(1)

    template = Path(args.template) if args.template else trust_dir / ".env.example"
    if not template.is_file():
        print(f"❌ Base template {template} not found.", file=sys.stderr)
        sys.exit(1)

    scaffold(target, template, args.name, args.code, args.region)
    print(f"✅ Scaffolded {target}")
    if args.env == "development":
        print(f"   Next: make register-trust KIT={args.code}")
    else:
        print(f"   Next: make -C deploy/providers/AWS register-trusts KIT={args.code} HUB={args.env}")


if __name__ == "__main__":
    main()
