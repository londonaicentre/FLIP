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

Picks the 12 Hub-shared values out of the caller's environment and upserts each
into trust/.env.<KIT>, under the sentinel header that distribute-trust-kits and
package-onprem-trust-kit both byte-match. Credentials are never touched — only
the 12 Hub-shared keys.

The caller (the root Makefile) is responsible for sourcing the right env file:
`include $(MAIN_ENV_FILE)` + `export $(shell sed ...)` populates os.environ
with .env.development / .env.stag / .env.production values before invoking
this script. No docker compose exec, no ECS round-trip, no per-trust
TRUST_<n>_NAME lookup — just a file→file copy keyed on KIT, portable across
dev/stag/prod.

Replaces the previous sync-trust-kit.sh + sync-trust-kits.sh pair (bash + jq).
Stdlib-only so `uv run` doesn't need to resolve any dependencies.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Keep this tuple in lockstep with HUB_SHARED_ENV_KEYS in
# flip_api/scripts/register_trust.py and the HUB_SHARED_KEYS array in
# deploy/providers/AWS/scripts/register-trusts.sh.
HUB_SHARED_KEYS: tuple[str, ...] = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_REGISTRY",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
)

# Sentinel comment marking the start of the managed Hub-shared block. Must be
# byte-identical to the sentinel in distribute-trust-kits.sh,
# register-trusts.sh, and package-onprem-trust-kit.sh.
SENTINEL = "# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──"


def die(msg: str) -> "type[never]":  # type: ignore[name-defined]
    """Print an error and exit 1."""
    print(msg, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kit", help="Slot name (e.g. Trust_2). Selects trust/.env.<kit>.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    target = repo_root / "trust" / f".env.{args.kit}"
    if not target.is_file():
        die(f"❌ Kit file {target} not found — create it from the matching .example first.")

    missing = [k for k in HUB_SHARED_KEYS if not os.environ.get(k)]
    if missing:
        die(
            f"❌ These Hub-shared keys are unset in the environment: {', '.join(missing)}\n"
            f"   Make sure the matching .env.<env> file is present and populated."
        )

    new_values = {k: os.environ[k] for k in HUB_SHARED_KEYS}

    # Surface the source env file + destination URL up-front. The root
    # Makefile prints `Using MAIN_ENV_FILE: .env.<env>` at the top of its
    # output, but that scrolls past easily — log it here too so the
    # KIT-keyed sync command is self-documenting. Doubles as a footgun
    # guard: on-prem-pointed kits (typically Trust_2) accidentally synced
    # without PROD=true would silently get the dev hub URL.
    source = os.environ.get("MAIN_ENV_FILE", "<environment>")
    hub_url = new_values["CENTRAL_HUB_API_URL"]
    print(f"🔄 Syncing hub-shared block for trust/.env.{args.kit} from {source}")
    print(f"   CENTRAL_HUB_API_URL → {hub_url}")
    if "localhost" in hub_url or "127.0.0.1" in hub_url:
        print("   ⚠️  Hub URL points at localhost — this is the dev hub.")
        print("       If this kit should target stag/prod, re-run with PROD=stag or PROD=true.")

    # Upsert: replace `KEY=...` lines in-place for keys already present,
    # append the rest below the sentinel header. Comments (`#`) and blank
    # lines pass through unchanged. Existing positions are preserved so a
    # second sync produces a byte-identical file when env values are stable.
    pending = dict(new_values)
    new_lines: list[str] = []
    for line in target.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in pending:
                new_lines.append(f"{key}={pending.pop(key)}")
                continue
        new_lines.append(line)

    if pending:
        # First sync (or a key the previous sync didn't write) — append the
        # sentinel header if it's missing, then the remaining KEY=value lines.
        if not any(line == SENTINEL for line in new_lines):
            new_lines.append("")
            new_lines.append(SENTINEL)
        for key, value in pending.items():
            new_lines.append(f"{key}={value}")

    target.write_text("\n".join(new_lines) + "\n")
    print(f"Synced hub-shared block in {target}")


if __name__ == "__main__":
    main()
