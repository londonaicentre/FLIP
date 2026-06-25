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
"""Write trust kit files from register_trust JSON output (stdlib only).

Replaces scripts/distribute-trust-kits.sh. Two modes:

- *Array mode (default)* — read a JSON array of kits on stdin (the stdout of
  ``flip_api.scripts.register_trust``) and write each to
  ``trust/.env.<fl_kit_slot>`` (seeding host-local profile from the matching
  ``.example`` on first write). Tolerates leading build/log noise; an empty
  array is a no-op. Used by the dev ``register-trust`` Makefile pipe.

- *``--target PATH`` (single kit)* — read one kit (a JSON object, or a
  one-element array) on stdin and write it to ``PATH``. Used by
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


def _last_array_line(text: str) -> str | None:
    """Return the last non-blank stdin line that looks like a JSON array.

    register_trust prints the kit array as a single line; build tooling may
    prepend log noise. Keeping the last ``[``-prefixed line drops that noise.

    Args:
        text (str): Raw stdin.

    Returns:
        str | None: The JSON-array line, or None if there is none.
    """
    candidates = [line for line in text.splitlines() if line.strip() and line.lstrip().startswith("[")]
    return candidates[-1] if candidates else None


def main() -> None:
    """CLI entry point: distribute kit JSON from stdin into kit file(s)."""
    parser = argparse.ArgumentParser(description="Write trust kit files from register_trust JSON output.")
    parser.add_argument("--target", default=None, help="Write a single kit (from stdin) to this exact path.")
    parser.add_argument("--example", default=None, help="Template to seed --target from when it is absent.")
    args = parser.parse_args()

    raw = sys.stdin.read()

    if args.target:
        data = json.loads(raw) if raw.strip() else None
        kit = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not kit:
            print("ℹ️  No kit on stdin — target unchanged.")
            return
        target = Path(args.target)
        example = Path(args.example) if args.example else None
        # Dev kits (trust/.env.<CODE>.development) source the hub-shared block from
        # the hub's .env.development (the single source), so write it commented and
        # inert here rather than a drifting live copy. Prod kits (.stag/.production)
        # carry it live — the remote operator's only source.
        commented = target.name.endswith(".development")
        tkl.write_kit(target, kit, example=example, hub_shared_commented=commented)
        print(f"✅ Wrote kit → {args.target}")
        return

    kits = json.loads(_last_array_line(raw) or "[]")
    if not kits:
        print("ℹ️  No kits to distribute (empty array — registration error?). Kit files unchanged.")
        return

    trust_dir = Path(__file__).resolve().parent.parent / "trust"
    for kit in kits:
        slot = kit["fl_kit_slot"]
        target = trust_dir / f".env.{slot}"
        example = trust_dir / f".env.{slot}.example"
        tkl.write_kit(target, kit, example=example if example.exists() else None)
        print(f"✅ Wrote kit for trust '{kit.get('trust_name')}' → {target}")
    print("✅ distribute-trust-kits complete.")


if __name__ == "__main__":
    main()
