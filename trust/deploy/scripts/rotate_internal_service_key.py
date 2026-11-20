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
"""Rotate a trust's TRUST_INTERNAL_SERVICE_KEY in its kit file, on the site alone (FLIP#1714).

The hub never sees or checks this key: only the trust's own services do, when trust-api calls
imaging-api and data-access-api. So a site can rotate it on its own schedule, with no hub call and
without touching TRUST_API_KEY. The new value takes effect when every trust container is recreated
from the kit together, because a container left on the old value would refuse its siblings' calls.

Usage (from trust/): ``make rotate-internal-service-key KIT=<CODE>``, or directly:
    python3 deploy/scripts/rotate_internal_service_key.py --kit-file .env.<CODE>.<env>

Stdlib only, like every kit writer: it runs on a bare trust host.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trust_kit_lib  # noqa: E402

KEY = "TRUST_INTERNAL_SERVICE_KEY"


def rotate(kit_file: Path) -> None:
    """Write a fresh internal service key into ``kit_file``, keeping every other line and the 0600 mode.

    Raises:
        FileNotFoundError: The kit file does not exist.
        ValueError: The kit has no live TRUST_INTERNAL_SERVICE_KEY line, so this is not a registered kit.
    """
    lines = kit_file.read_text().splitlines()
    if not any(not ln.lstrip().startswith("#") and ln.split("=", 1)[0] == KEY for ln in lines):
        raise ValueError(f"{kit_file} has no {KEY} line; register the trust before rotating its keys")
    # Same shape as the hub mints at registration (flip_api.scripts.generate_trust_key).
    trust_kit_lib.write_secure(kit_file, trust_kit_lib.upsert(lines, KEY, secrets.token_urlsafe(32)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kit-file", type=Path, required=True, help="the trust's kit file, trust/.env.<CODE>.<env>")
    args = parser.parse_args(argv)
    try:
        rotate(args.kit_file)
    except (OSError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"✅ New {KEY} written to {args.kit_file}. Recreate every trust container together from it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
