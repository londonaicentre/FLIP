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

import contextlib
import json
import os
import tempfile
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
    """Replace *path* with *lines* atomically, as a 0600 file, so a kit is never partial or exposed.

    The content goes to a temp file in the same directory, created 0600, which is flushed, fsynced
    and renamed over the file with ``os.replace``. An interrupted write leaves the old file whole,
    and generated credentials never sit in a file with a wider mode. A symlinked file is written
    through to the file it names, and the replacement keeps the old file's owner when this process
    may set it.

    ``trust/deploy/scripts/trust_kit_lib.write_secure`` is the same routine for the root-level ``register-trust``
    scripts. It is duplicated rather than shared because that module lives at the repository root,
    outside this package and its virtual environment, so neither can import the other.

    Args:
        path (Path): The env file to write.
        lines (list[str]): Lines to write, without trailing newlines.

    Returns:
        None
    """
    # Write a 0600 temp file beside the kit and rename it over the kit, so an interrupted write leaves
    # the old kit whole: it may be the only plaintext copy of TRUST_API_KEY. The rename goes to the
    # file a symlinked kit names (setup moves kits to /etc/flip/trust and leaves a link), not over
    # the link, and the new file keeps the old one's owner where this process may set it.
    real = os.path.realpath(path)
    directory = os.path.dirname(real)
    try:
        previous = os.stat(real)
    except FileNotFoundError:
        previous = None
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as env_file:
            # mkstemp already creates 0600; set it explicitly so the mode never depends on that.
            os.fchmod(env_file.fileno(), KIT_FILE_MODE)
            if previous is not None and (previous.st_uid, previous.st_gid) != (os.getuid(), os.getgid()):
                with contextlib.suppress(PermissionError):
                    os.fchown(env_file.fileno(), previous.st_uid, previous.st_gid)
            # Guard the empty case so an empty list writes an empty file, not a lone "\n" that would
            # leave a leading blank line; the two copies of this routine must stay identical.
            env_file.write("\n".join(lines) + "\n" if lines else "")
            env_file.flush()
            os.fsync(env_file.fileno())
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:  # noqa: PLR2004
        print(f"Usage: {sys.argv[0]} <json_string> <key>", file=sys.stderr)
        sys.exit(1)
    print(get_json_value(sys.argv[1], sys.argv[2]))
