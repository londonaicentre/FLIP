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
profile + mock-stack default credentials + ``<run-make-register-trust>``
placeholders for the hub-managed blocks) and prepending the trust's identity
(``TRUST_NAME`` / ``TRUST_CODE`` / ``TRUST_REGION``).

The result is ready for ``make register-trust KIT=<CODE> PROD=<env>`` to fill
in the credentials, FL kit slot, and hub-shared block. Refuses to overwrite an
existing kit. Driven by ``make new-trust``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    """CLI entry point: scaffold trust/.env.<CODE>.<env> from the base template."""
    parser = argparse.ArgumentParser(description="Scaffold a new trust kit file.")
    parser.add_argument("--code", required=True, help="Trust short code — the kit-file handle (e.g. KCH).")
    parser.add_argument("--name", required=True, help="Trust display name registered on the hub.")
    parser.add_argument("--region", default=None, help="Optional NHS region.")
    parser.add_argument("--env", required=True, choices=("development", "stag", "production"), help="Target env.")
    parser.add_argument("--template", default=None, help="Base template (default trust/.env.example).")
    parser.add_argument("--trust-dir", default=None, help="Override the trust/ directory (testing).")
    args = parser.parse_args()

    trust_dir = Path(args.trust_dir) if args.trust_dir else Path(__file__).resolve().parent.parent / "trust"
    target = trust_dir / f".env.{args.code}.{args.env}"
    if target.exists():
        print(f"❌ {target} already exists — refusing to overwrite (delete it first).", file=sys.stderr)
        sys.exit(1)

    template = Path(args.template) if args.template else trust_dir / ".env.example"
    if not template.is_file():
        print(f"❌ Base template {template} not found.", file=sys.stderr)
        sys.exit(1)

    identity = [
        "# ── Trust identity (sent to the hub by `make register-trust`) ──",
        f"TRUST_NAME={args.name}",
        f"TRUST_CODE={args.code}",
    ]
    if args.region:
        identity.append(f"TRUST_REGION={args.region}")
    identity.append("")  # blank line before the template body

    target.write_text("\n".join(identity) + "\n" + template.read_text())
    target.chmod(0o600)
    print(f"✅ Scaffolded {target}")
    print(f"   Next: make register-trust KIT={args.code} PROD={'true' if args.env == 'production' else args.env}")


if __name__ == "__main__":
    main()
