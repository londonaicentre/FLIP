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

"""Shared utilities for reading and updating environment files."""

import json
import os
from pathlib import Path

# Owner read/write only. Kit files carry generated database passwords, per-trust API keys and the
# trust-internal service key, so their mode must never be left to the process umask.
KIT_FILE_MODE = 0o600


def get_json_value(json_str: str, key: str) -> str:
    """Extract a value from a JSON dict string.

    Args:
        json_str (str): A JSON-encoded dict (e.g. '{"Trust_1": "key1"}').
        key (str): The key to look up.

    Returns:
        str: The value for *key*, or an empty string if the key is missing
        or *json_str* is empty.
    """
    return json.loads(json_str or "{}").get(key, "")


def read_env_value(lines: list[str], var_name: str) -> str | None:
    """Read the value of a variable from env file lines.

    Args:
        lines (list[str]): Lines of the environment file.
        var_name (str): Variable name to look up.

    Returns:
        str | None: The value if found, else None.
    """
    for line in lines:
        if line.startswith(f"{var_name}="):
            return line.split("=", 1)[1].strip()
    return None


def update_or_append(lines: list[str], var_name: str, value: str) -> list[str]:
    """Update an existing env var line or append it.

    Args:
        lines (list[str]): Lines of the environment file.
        var_name (str): Variable name to set.
        value (str): New value for the variable.

    Returns:
        list[str]: Updated lines.
    """
    new_lines: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{var_name}="):
            new_lines.append(f"{var_name}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{var_name}={value}")
    return new_lines


def write_env_file(path: Path, lines: list[str]) -> None:
    """Write env file lines to *path* and restrict it to owner read/write.

    A kit file created outside the sanctioned paths can keep whatever mode the umask gave it —
    0644 on a stock box — while receiving generated credentials. Tighten the open file descriptor
    before truncating or writing it, so the new contents are never exposed through an existing
    permissive mode.

    ``scripts/trust_kit_lib.write_secure`` is the same routine for the root-level ``register-trust``
    scripts. It is duplicated rather than shared because that module lives at the repository root,
    outside this package and its virtual environment, so neither can import the other.

    Args:
        path (Path): The env file to write.
        lines (list[str]): Lines to write, without trailing newlines.

    Returns:
        None
    """
    # Do not use Path.write_text followed by chmod: an existing 0644 file, or a new file created
    # under umask 022, would contain fresh credentials before its mode is tightened. Opening
    # without O_TRUNC lets fchmod run first; truncate and write only after the descriptor is 0600.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, KIT_FILE_MODE)
    try:
        os.fchmod(fd, KIT_FILE_MODE)
        os.ftruncate(fd, 0)
    except Exception:
        os.close(fd)
        raise

    with os.fdopen(fd, "w") as env_file:
        env_file.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:  # noqa: PLR2004
        print(f"Usage: {sys.argv[0]} <json_string> <key>", file=sys.stderr)
        sys.exit(1)
    print(get_json_value(sys.argv[1], sys.argv[2]))
