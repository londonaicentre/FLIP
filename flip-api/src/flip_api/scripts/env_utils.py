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


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:  # noqa: PLR2004
        print(f"Usage: {sys.argv[0]} <json_string> <key>", file=sys.stderr)
        sys.exit(1)
    print(get_json_value(sys.argv[1], sys.argv[2]))
