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

"""Generate per-trust API keys and write them into an environment file.

Trust names are read from the ``TRUST_NAMES`` env var (a JSON list).  Both
plaintext keys (``TRUST_API_KEYS``) and their hashes (``TRUST_API_KEY_HASHES``)
are written as JSON dicts into the environment file.

Usage:
    make generate-trust-api-keys
    make generate-trust-api-keys ENV_FILE=.env.stag
"""

import argparse
import hashlib
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
    """Generate per-trust API keys and write them into the specified environment file.

    Reads trust names from ``TRUST_NAMES``, generates a cryptographically secure
    key for each trust that doesn't already have a value in the ``TRUST_API_KEYS``
    dict (or all if ``--force``), and updates both ``TRUST_API_KEYS`` and
    ``TRUST_API_KEY_HASHES`` JSON dicts in the env file.

    Raises:
        SystemExit: If the env file is missing or contains no ``TRUST_NAMES`` entry.
    """
    parser = argparse.ArgumentParser(description="Generate per-trust API keys and update an environment file.")
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

    # Parse existing TRUST_API_KEYS dict (if any)
    existing_keys_json = read_env_value(lines, "TRUST_API_KEYS")
    existing_keys: dict[str, str] = json.loads(existing_keys_json) if existing_keys_json else {}

    # Generate keys — existing values in the dict are preserved unless --force
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

    # Build both JSON dicts
    hashes_dict = {name: hashlib.sha256(keys_dict[name].encode()).hexdigest() for name in trust_names}
    lines = update_or_append(lines, "TRUST_API_KEYS", json.dumps(keys_dict))
    lines = update_or_append(lines, "TRUST_API_KEY_HASHES", json.dumps(hashes_dict))

    env_file.write_text("\n".join(lines) + "\n")

    generated = sum(1 for a in actions.values() if a == "generated")
    skipped = sum(1 for a in actions.values() if a == "skipped")
    print(f"Updated {env_file.name}: {generated} trust keys generated, {skipped} skipped (already set).")
    for name, action in actions.items():
        print(f"  {name}: {action}")
    if generated:
        print(f"  TRUST_API_KEYS and TRUST_API_KEY_HASHES updated with {len(trust_names)} entries")


if __name__ == "__main__":
    main()
