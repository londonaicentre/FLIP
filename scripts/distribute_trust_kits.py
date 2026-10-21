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
"""Write a trust kit file from register_trust JSON output (stdlib only).

Reads one kit (a JSON object, or a one-element array) on stdin and writes it to
``--target PATH``. Used by the dev ``register-trust`` Makefile pipe and by
deploy/providers/AWS/scripts/register-trusts.sh for the local-file and
ec2-tempfile writes (after it has fetched the kit out of SSM).

All file writing delegates to ``trust_kit_lib.write_kit`` — the single
in-place-upsert implementation that preserves operator edits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trust_kit_lib as tkl  # noqa: E402


def main() -> None:
    """CLI entry point: write the kit JSON on stdin into the --target kit file."""
    parser = argparse.ArgumentParser(description="Write a trust kit file from register_trust JSON output.")
    parser.add_argument("--target", required=True, help="Write the kit (from stdin) to this exact path.")
    parser.add_argument("--example", default=None, help="Template to seed --target from when it is absent.")
    args = parser.parse_args()

    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else None
    kit = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    if not kit:
        print("ℹ️  No kit on stdin — target unchanged.")
        return
    target = Path(args.target)
    example = Path(args.example) if args.example else None
    # Dev kits (trust/.env.<CODE>.development) source the hub-shared block from
    # the hub's .env.development (the single source), so write it commented and
    # inert here rather than a drifting live copy. Deployed kits (.stag/.prod)
    # carry it live — the remote operator's only source.
    commented = target.name.endswith(".development")
    tkl.write_kit(target, kit, example=example, hub_shared_commented=commented)
    print(f"✅ Wrote kit → {args.target}")


if __name__ == "__main__":
    main()
