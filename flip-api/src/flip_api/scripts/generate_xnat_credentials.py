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

"""Generate the XNAT Postgres + ActiveMQ credentials in an environment file.

The XNAT trust services (xnat-web + xnat-db) require three passwords:

* ``XNAT_DATASOURCE_PASSWORD`` — XNAT app's connection password to xnat-db.
* ``XNAT_DATASOURCE_ADMIN_PASSWORD`` — Postgres superuser password on xnat-db.
* ``XNAT_ACTIVEMQ_PASSWORD`` — ActiveMQ broker password used by XNAT for async work.

These were previously committed to the repo with weak defaults and baked into
the published Docker image at build time. They are now generated at deploy
time and loaded into the containers as runtime environment variables only.

Usage:
    make generate-xnat-credentials
    make generate-xnat-credentials ENV_FILE=.env.stag
    make generate-xnat-credentials FORCE=1
"""

import argparse
import secrets
import sys
from pathlib import Path

from flip_api.scripts.env_utils import read_env_value, update_or_append

REPO_ROOT = Path(__file__).resolve().parents[4]

PASSWORD_VARS = (
    "XNAT_DATASOURCE_PASSWORD",
    "XNAT_DATASOURCE_ADMIN_PASSWORD",
    "XNAT_ACTIVEMQ_PASSWORD",
)

PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "<run-make-generate-xnat-credentials>",
        "xnat",
        "password",
        "admin",
    }
)


def _is_placeholder(value: str | None) -> bool:
    """Return True when the variable is unset or has a known placeholder/weak value.

    Args:
        value (str | None): The current value of the env var.

    Returns:
        bool: ``True`` when the value should be regenerated.
    """
    return value is None or value in PLACEHOLDER_VALUES


def main() -> None:
    """Generate the XNAT credentials and update the environment file.

    Existing per-variable values are preserved unless ``--force`` is given or
    the value is a known placeholder/weak default.

    Raises:
        SystemExit: If the env file is missing.
    """
    parser = argparse.ArgumentParser(
        description="Generate XNAT Postgres + ActiveMQ credentials and update an environment file.",
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
        help="Regenerate every password even if it already has a non-placeholder value.",
    )
    args = parser.parse_args()
    env_file: Path = args.env_file

    if not env_file.exists():
        print(f"Error: {env_file} not found.")
        sys.exit(1)

    lines = env_file.read_text().splitlines()

    actions: dict[str, str] = {}
    for var in PASSWORD_VARS:
        existing = read_env_value(lines, var)
        if not args.force and not _is_placeholder(existing):
            actions[var] = "skipped"
            continue
        new_value = secrets.token_urlsafe(32)
        lines = update_or_append(lines, var, new_value)
        actions[var] = "generated"

    env_file.write_text("\n".join(lines) + "\n")

    generated = sum(1 for a in actions.values() if a == "generated")
    skipped = sum(1 for a in actions.values() if a == "skipped")
    print(f"Updated {env_file.name}: {generated} XNAT credentials generated, {skipped} skipped.")
    for var, action in actions.items():
        print(f"  {var}: {action}")


if __name__ == "__main__":
    main()
