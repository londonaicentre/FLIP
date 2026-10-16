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
"""Refresh the Hub-shared block in trust/.env.<KIT> from environment variables.

Usage:
    uv run scripts/sync_trust_kit.py <KIT>

Picks the Hub-shared values out of the caller's environment and upserts each
into trust/.env.<KIT> under the managed sentinel — credentials and operator
edits are left untouched. The actual file merge is delegated to
``trust_kit_lib.write_kit`` (the single kit-writer), so the key set and sentinel
have one definition.

The caller (the root Makefile) is responsible for sourcing the right env file:
`include $(MAIN_ENV_FILE)` + `export` populates os.environ with
.env.development / .env.stag / .env.production (or .env.lza-prod / .env.lza-stag)
values before invoking this script. No docker compose exec, no ECS round-trip, no
per-trust lookup — just a file→file copy keyed on KIT, portable across dev/stag/prod.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trust_kit_lib as tkl  # noqa: E402


def die(msg: str) -> NoReturn:
    """Print an error and exit 1.

    Args:
        msg (str): Message to print to stderr.

    Returns:
        NoReturn: Never returns (raises SystemExit).
    """
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    """CLI entry point: refresh the Hub-shared block in trust/.env.<KIT>."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kit", help="Slot/handle (e.g. Trust_2). Selects trust/.env.<kit>.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "trust" / f".env.{args.kit}"
    if not target.is_file():
        die(f"❌ Kit file {target} not found — create it from the matching .example first.")

    # FL_EDGE_PORT, the port trusts dial (FLIP#1365), defaults to FL_SERVER_PORT as register_trust
    # does, except on LZA staging: its edge serves staging on :8003 and :8002 is production's.
    edge_port = os.environ.get("FL_EDGE_PORT", "").strip()
    if not edge_port:
        if args.kit.endswith(".lza-stag"):
            die("❌ FL_EDGE_PORT is unset. Set FL_EDGE_PORT=8003 in .env.lza-stag (the edge's staging listener).")
        edge_port = os.environ.get("FL_SERVER_PORT", "").strip()
    if edge_port and not edge_port.isdigit():
        die(f"❌ FL_EDGE_PORT must be a port number, got {edge_port!r}.")
    if edge_port:
        os.environ["FL_EDGE_PORT"] = edge_port

    # Require every hub-shared key: forgetting to source the env file leaves them unset.
    missing = [k for k in tkl.HUB_SHARED_KEYS if not os.environ.get(k)]
    if missing:
        die(
            f"❌ These Hub-shared keys are unset in the environment: {', '.join(missing)}\n"
            f"   Make sure the matching .env.<env> file is present and populated."
        )

    new_values = {k: os.environ[k] for k in tkl.HUB_SHARED_KEYS}

    # Surface the source env file + destination URL up-front — doubles as a
    # footgun guard: an on-prem-pointed kit accidentally synced without PROD=true
    # would silently get the dev hub URL.
    source = os.environ.get("MAIN_ENV_FILE", "<environment>")
    hub_url = new_values["CENTRAL_HUB_API_URL"]
    print(f"🔄 Syncing hub-shared block for trust/.env.{args.kit} from {source}")
    print(f"   CENTRAL_HUB_API_URL → {hub_url}")
    if "localhost" in hub_url or "127.0.0.1" in hub_url:
        print("   ⚠️  Hub URL points at localhost — this is the dev hub.")
        print("       If this kit should target stag/prod, re-run with PROD=stag or PROD=true.")

    # Delegate the in-place merge: only the hub-shared block is passed, so
    # credentials and host-local edits are never touched.
    tkl.write_kit(target, {"hub_shared": new_values})
    print(f"Synced hub-shared block in {target}")


if __name__ == "__main__":
    main()
