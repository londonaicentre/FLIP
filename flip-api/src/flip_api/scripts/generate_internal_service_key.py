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

"""Generate the internal service key used by fl-server to authenticate with flip-api.

Both ``INTERNAL_SERVICE_KEY`` (plaintext) and ``INTERNAL_SERVICE_KEY_HASH``
(SHA-256 hex digest) are written into the environment file.  On subsequent
runs the script checks that the two values are in sync and skips if they are.

Usage:
    make generate-internal-service-key
    make generate-internal-service-key ENV_FILE=.env.stag
    make generate-internal-service-key FORCE=1
"""

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

from flip_api.scripts.env_utils import read_env_value, update_or_append

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    """Generate the internal service key and update the environment file.

    Raises:
        SystemExit: If the env file is missing.
    """
    parser = argparse.ArgumentParser(description="Generate internal service key and update an environment file.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env.development",
        help="Path to the environment file to update (default: .env.development)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the key even if it already exists and is in sync.",
    )
    args = parser.parse_args()
    env_file: Path = args.env_file

    if not env_file.exists():
        print(f"Error: {env_file} not found.")
        sys.exit(1)

    lines = env_file.read_text().splitlines()
    existing_key = read_env_value(lines, "INTERNAL_SERVICE_KEY")
    existing_hash = read_env_value(lines, "INTERNAL_SERVICE_KEY_HASH")

    if existing_key and not args.force:
        expected_hash = hashlib.sha256(existing_key.encode()).hexdigest()
        if existing_hash == expected_hash:
            print(f"Internal service key already in sync in {env_file.name}")
            return
        # Key exists but hash is stale — re-sync the hash
        print(f"Key exists but hash is out of sync — updating hash in {env_file.name}")
        lines = update_or_append(lines, "INTERNAL_SERVICE_KEY_HASH", expected_hash)
        env_file.write_text("\n".join(lines) + "\n")
        print(f"  INTERNAL_SERVICE_KEY_HASH updated in {env_file.name}")
        return

    # Generate a new key
    key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    lines = update_or_append(lines, "INTERNAL_SERVICE_KEY", key)
    lines = update_or_append(lines, "INTERNAL_SERVICE_KEY_HASH", key_hash)
    env_file.write_text("\n".join(lines) + "\n")

    print("Generated internal service key:")
    print(f"  INTERNAL_SERVICE_KEY and INTERNAL_SERVICE_KEY_HASH updated in {env_file.name}")


if __name__ == "__main__":
    main()
