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

"""Generate the key flip-api presents to the FL APIs (``FL_API_KEY``).

Every FL API route but /health requires it, so ``make up`` mints one into the environment file
before compose runs. An existing value is kept unless ``--force``.

Usage:
    make generate-fl-api-key
    make generate-fl-api-key ENV_FILE=.env.stag
    make generate-fl-api-key FORCE=1
"""

import argparse
import secrets
import sys
from pathlib import Path

from flip_api.scripts.env_utils import read_env_value, update_or_append, write_env_file

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    """Generate the FL API key and update the environment file.

    Raises:
        SystemExit: If the env file is missing.
    """
    parser = argparse.ArgumentParser(description="Generate the FL API key and update an environment file.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env.development",
        help="Path to the environment file to update (default: .env.development)",
    )
    parser.add_argument("--force", action="store_true", help="Regenerate the key even if one is set.")
    args = parser.parse_args()
    env_file: Path = args.env_file

    if not env_file.exists():
        print(f"Error: {env_file} not found.")
        sys.exit(1)

    lines = env_file.read_text().splitlines()
    if read_env_value(lines, "FL_API_KEY") and not args.force:
        print(f"FL API key already set in {env_file.name}")
        return

    lines = update_or_append(lines, "FL_API_KEY", secrets.token_urlsafe(32))
    write_env_file(env_file, lines)
    print(f"Generated FL API key: FL_API_KEY updated in {env_file.name}")


if __name__ == "__main__":
    main()
