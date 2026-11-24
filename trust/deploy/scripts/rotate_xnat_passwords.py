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
"""Mint new XNAT admin and service passwords into a trust's kit file (FLIP#1569).

Both passwords are trust-local: the hub never sees them. ``configure-xnat.sh`` applies them to a running
XNAT on the next deploy or upgrade:

- the service password: the account already exists, so the run signs in with the kit's value and, when
  XNAT refuses it, sets it as admin;
- the admin password: this script moves the value XNAT holds into ``XNAT_ADMIN_PREVIOUS_PASSWORD``. The
  run signs in with that once, changes the admin password to the new ``XNAT_ADMIN_PASSWORD``, and from
  then on the previous value is never used. Remove the line after a successful deploy.

The two new values are minted independently, so the admin and service accounts never share one.

Run it on the copy of the kit the trust is deployed from, and redistribute that copy if it lives
elsewhere: a kit delivered later from an older copy would put the old passwords back. Then deploy:
``make upgrade-onprem-trust KIT=<CODE>`` (Compose), or ``scripts/generate_values.py`` and
``make -C trust/deploy/helm deploy-trust-k8s KIT=<CODE>`` (Helm). Snowflake trusts do not carry
``XNAT_ADMIN_PREVIOUS_PASSWORD``: use ``--service-only`` there, and change the admin password by hand.

Between the deploy and the end of ``configure-xnat.sh``, imaging-api already holds the new service
password and XNAT still the old one, so its calls fail with 401. Each one is a failed login, and XNAT
locks an account after about 20: rotate in a quiet window.

Usage (from trust/): ``make rotate-xnat-passwords KIT=<CODE> HUB=<env> [SERVICE_ONLY=1]``, or directly:
    python3 deploy/scripts/rotate_xnat_passwords.py --kit-file .env.<CODE>.<env> [--service-only]

Stdlib only, like every kit writer: it runs on a bare trust host.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trust_kit_lib  # noqa: E402

ADMIN = "XNAT_ADMIN_PASSWORD"
PREVIOUS = "XNAT_ADMIN_PREVIOUS_PASSWORD"
SERVICE = "XNAT_SERVICE_PASSWORD"


def _live_values(lines: list[str]) -> dict[str, str]:
    """The kit's ``KEY=value`` lines that are not commented out (the last one wins, as in Make and Compose)."""
    values: dict[str, str] = {}
    for line in lines:
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _mint(*avoid: str) -> str:
    """A new password, the same shape new_trust.py mints, never equal to any of ``avoid``."""
    while True:
        value = secrets.token_urlsafe(24)
        if value not in avoid:
            return value


def rotate(kit_file: Path, service_only: bool = False) -> None:
    """Write new XNAT passwords into ``kit_file``, keeping every other line and the 0600 mode.

    Args:
        kit_file (Path): The trust's kit file.
        service_only (bool): Rotate the service password alone and leave the admin one as it is.

    Raises:
        FileNotFoundError: The kit file does not exist.
        ValueError: The kit lacks a live XNAT admin or service password, or an admin rotation is
            still pending: rotating again would drop the only record of the password XNAT holds.
    """
    lines = kit_file.read_text().splitlines()
    values = _live_values(lines)
    missing = [key for key in (ADMIN, SERVICE) if not values.get(key)]
    if missing:
        raise ValueError(f"{kit_file} has no {' or '.join(missing)}; this is not a trust kit")
    if not service_only and values.get(PREVIOUS):
        raise ValueError(
            f"{kit_file} already has {PREVIOUS}: an admin password rotation is pending. Deploy it first; "
            f"once configure-xnat.sh reports 'Admin password changed', remove the {PREVIOUS} line and run this again"
        )
    old_admin, old_service = values[ADMIN], values[SERVICE]
    if service_only:
        lines = trust_kit_lib.upsert(lines, SERVICE, _mint(old_service, old_admin))
    else:
        new_admin = _mint(old_admin, old_service)
        lines = trust_kit_lib.upsert(lines, PREVIOUS, old_admin)
        lines = trust_kit_lib.upsert(lines, ADMIN, new_admin)
        lines = trust_kit_lib.upsert(lines, SERVICE, _mint(old_service, old_admin, new_admin))
    trust_kit_lib.write_secure(kit_file, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit-file", type=Path, required=True, help="the trust's kit file, trust/.env.<CODE>.<env>")
    parser.add_argument(
        "--service-only", action="store_true", help="rotate XNAT_SERVICE_PASSWORD alone, leaving the admin password"
    )
    args = parser.parse_args(argv)
    try:
        rotate(args.kit_file, service_only=args.service_only)
    except (OSError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    rotated = SERVICE if args.service_only else f"{ADMIN} and {SERVICE} (old admin value kept as {PREVIOUS})"
    print(f"✅ New {rotated} written to {args.kit_file}. Deploy the trust from it to apply them to XNAT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
