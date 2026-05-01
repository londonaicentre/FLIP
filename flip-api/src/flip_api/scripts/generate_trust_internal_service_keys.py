# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Generate per-trust internal-service keys used across the trust services.

This is the trust-side analogue of ``generate_internal_service_key.py`` (which
covers the hub's fl-server → flip-api boundary). Each trust gets a distinct
plaintext key shared by every trust-internal service (trust-api, imaging-api,
data-access-api, fl-client) — the receiver does a constant-time compare against
its own copy. The hub never sees these keys.

Trust names are read from the ``TRUST_NAMES`` env var (a JSON list). The
plaintext keys are written as a JSON dict into ``TRUST_INTERNAL_SERVICE_KEYS``
in the environment file. ``trust/Makefile`` extracts the per-trust value at
deploy time via ``get_json_value``, the same way it already handles
``TRUST_API_KEYS``.

Usage:
    make generate-trust-internal-service-keys
    make generate-trust-internal-service-keys ENV_FILE=.env.stag
    make generate-trust-internal-service-keys FORCE=1
"""

import argparse
import json
import sys
from pathlib import Path

from flip_api.scripts.env_utils import read_env_value, update_or_append
from flip_api.scripts.generate_trust_key import generate_trust_key

REPO_ROOT = Path(__file__).resolve().parents[4]


def _parse_trust_names(lines: list[str]) -> list[str]:
    """Extract trust names from the TRUST_NAMES env var line.

    Args:
        lines (list[str]): Lines of the environment file.

    Returns:
        list[str]: List of trust names, e.g. ``["Trust_1", "Trust_2"]``.
    """
    for line in lines:
        if line.startswith("TRUST_NAMES="):
            return json.loads(line.split("=", 1)[1])
    return []


def main() -> None:
    """Generate per-trust internal service keys and update the environment file.

    Existing per-trust keys are preserved unless ``--force`` is given.

    Raises:
        SystemExit: If the env file is missing or contains no ``TRUST_NAMES`` entry.
    """
    parser = argparse.ArgumentParser(
        description="Generate per-trust internal-service keys and update an environment file.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env.development",
        help="Path to the environment file to update (default: .env.development)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Regenerate all keys even if they already exist in the env file.",
    )
    args = parser.parse_args()
    env_file: Path = args.env_file

    if not env_file.exists():
        print(f"Error: {env_file} not found.")
        sys.exit(1)

    lines = env_file.read_text().splitlines()

    trust_names = _parse_trust_names(lines)
    if not trust_names:
        print(f"Error: no TRUST_NAMES entry found in {env_file.name}.")
        sys.exit(1)

    existing_keys_json = read_env_value(lines, "TRUST_INTERNAL_SERVICE_KEYS")
    existing_keys: dict[str, str] = json.loads(existing_keys_json) if existing_keys_json else {}

    keys_dict: dict[str, str] = {}
    actions: dict[str, str] = {}
    for trust_name in trust_names:
        existing_key = existing_keys.get(trust_name)
        if not args.force and existing_key:
            keys_dict[trust_name] = existing_key
            actions[trust_name] = "skipped"
        else:
            key, _ = generate_trust_key()
            keys_dict[trust_name] = key
            actions[trust_name] = "generated"

    lines = update_or_append(lines, "TRUST_INTERNAL_SERVICE_KEYS", json.dumps(keys_dict))

    env_file.write_text("\n".join(lines) + "\n")

    generated = sum(1 for a in actions.values() if a == "generated")
    skipped = sum(1 for a in actions.values() if a == "skipped")
    print(f"Updated {env_file.name}: {generated} trust internal-service keys generated, {skipped} skipped.")
    for name, action in actions.items():
        print(f"  {name}: {action}")
    if generated:
        print(f"  TRUST_INTERNAL_SERVICE_KEYS updated with {len(trust_names)} entries")


if __name__ == "__main__":
    main()
