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
"""Single kit-file writer for trust/.env.<...> files (stdlib only).

One in-place-upsert implementation behind every operator-side kit write —
distribute_trust_kits.py (dev and prod registration), sync_trust_kit.py (hub-shared
refresh) and site_upgrade.py (release tag pin). Merges a kit
dict (as emitted by ``flip_api.scripts.register_trust``) into a kit file while
preserving the operator's host-local edits:

- Credentials (``TRUST_API_KEY`` / ``TRUST_INTERNAL_SERVICE_KEY``) are written
  only when present in the kit (a new registration). The idempotent skip path
  omits them, so an existing kit's credentials are never clobbered (the hub
  stores only hashes and cannot re-emit plaintext).
- Metadata (``EXPECTED_TRUST_ID`` / ``FL_KIT_SLOT`` / ``FL_KIT_SLOT_NUMBER``)
  is present on both paths and upserted unconditionally.
- The hub-shared block is upserted under a sentinel header; the header is added
  once on first write.

Also the one Python reader of the kit's TRUST_DATA_SEED (:func:`trust_data_seed`).
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

# Mode for any file holding trust credentials: owner read/write only.
KIT_FILE_MODE = 0o600

# Sentinel comment marking the start of the managed Hub-shared block. Must stay
# byte-identical to the sentinel emitted historically so an existing kit's block
# is upserted in place rather than duplicated.
SENTINEL = "# ── Hub-shared (managed by register-trust / sync-trust-kits — do not edit) ──"

# One-line note placed under the sentinel in DEV kits, where the hub-shared vars
# are commented out (inherited from the hub's .env.development instead of copied
# here, to avoid a drifting snapshot). Production kits carry them live.
HUB_SHARED_DEV_NOTE = (
    "# (dev) inherited from the hub's .env.development (the single source) — left "
    "commented to avoid a drifting copy. Production kits set these live."
)

# The closed set of hub-shared keys. Kept in lockstep with
# flip-api/src/flip_api/scripts/register_trust.py:HUB_SHARED_ENV_KEYS (the emitter, which
# runs inside the flip-api container and cannot import this module). The
# lockstep is asserted by flip-api's test_register_trust_cli.py.
HUB_SHARED_KEYS: tuple[str, ...] = (
    "AES_KEY_BASE64",
    "CENTRAL_HUB_API_URL",
    "TRUST_API_KEY_HEADER",
    "FL_BACKEND",
    "FLOWER_KIT_DATE",
    "FLARE_KIT_DATE",
    "DOCKER_TAG",
    "DOCKER_FL_TAG",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
    "FL_EDGE_PORT",
)

# Plaintext credentials — written once on a new registration, never on skip.
CREDENTIAL_KEYS: tuple[str, ...] = ("TRUST_API_KEY", "TRUST_INTERNAL_SERVICE_KEY")

# Metadata — present on both the new-registration and idempotent-skip paths.
METADATA_KEYS: tuple[str, ...] = ("EXPECTED_TRUST_ID", "FL_KIT_SLOT", "FL_KIT_SLOT_NUMBER")

# Maps each managed env key to the field name in the kit dict it is sourced from.
_FIELD_BY_ENV_KEY: dict[str, str] = {
    "TRUST_API_KEY": "trust_api_key",  # pragma: allowlist secret
    "TRUST_INTERNAL_SERVICE_KEY": "trust_internal_service_key",  # pragma: allowlist secret
    "EXPECTED_TRUST_ID": "trust_id",
    "FL_KIT_SLOT": "fl_kit_slot",
    "FL_KIT_SLOT_NUMBER": "fl_kit_slot_number",
}

# The kit file is read by make and by compose, which both expand `$`, and a value is one line of it.
_UNSAFE_VALUE = re.compile(r"[\r\n$`\\]")


def unsafe_value(value: object) -> bool:
    """Whether ``value`` could not be written as one line of a kit file that make and compose read as it stands."""
    return not isinstance(value, (str, int)) or bool(_UNSAFE_VALUE.search(str(value)))


def trust_data_seed(kit: dict[str, str]) -> bool:
    """Whether the kit seeds the sample data: its TRUST_DATA_SEED (FLIP#1687), read as make reads it.

    A trailing ``# comment`` and every quote are dropped and unset is true (deploy/env_mode.mk's
    trust_data_seed). Anything but true or false is refused: read as true, it would seed a real
    trust's OMOP.

    Raises:
        ValueError: If the value is neither true nor false.
    """
    value = kit.get("TRUST_DATA_SEED", "").split("#", 1)[0].replace('"', "").replace("'", "").strip()
    if value not in ("", "true", "false"):
        raise ValueError(f"TRUST_DATA_SEED must be true or false, not {value!r}")
    return value != "false"


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    """Replace every live ``KEY=...`` line in place, or append ``KEY=value``.

    Comment and blank lines pass through untouched. Existing positions are
    preserved so a repeated write produces a stable file when values are stable.

    Args:
        lines (list[str]): Current file lines (no trailing newlines).
        key (str): Env var name to upsert.
        value (str): Value to set.

    Returns:
        list[str]: A new list of lines with the key upserted.
    """
    out: list[str] = []
    replaced = False
    for line in lines:
        if not line.lstrip().startswith("#") and line.split("=", 1)[0] == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def comment_out(lines: list[str], key: str) -> list[str]:
    """Replace the first ``KEY=...`` *or* ``# KEY=...`` line with ``# KEY=`` (no value).

    Used for dev kits: the hub-shared vars are documented but inert, since their
    values come from the hub's .env.development — a live copy here would only
    drift. Collapses any prior live/placeholder/commented line to ``# KEY=`` and
    appends it when absent, so it is idempotent across re-runs.

    Args:
        lines (list[str]): Current file lines (no trailing newlines).
        key (str): Env var name to comment out.

    Returns:
        list[str]: A new list of lines with the key present only as ``# KEY=``.
    """
    out: list[str] = []
    replaced = False
    for line in lines:
        bare = line.lstrip("#").lstrip()
        if not replaced and bare.split("=", 1)[0] == key:
            out.append(f"# {key}=")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"# {key}=")
    return out


def write_secure(target: Path, lines: list[str]) -> None:
    """Replace ``target`` with ``lines`` atomically, as a 0600 file, so a kit is never partial or exposed.

    The content goes to a temp file in the same directory, created 0600, which is flushed, fsynced
    and renamed over the kit with ``os.replace``. An interrupted write (a full disk, a killed
    process, a power cut) leaves the old kit whole rather than truncated, and the new credentials
    never sit in a file with a wider mode: ``Path.write_text`` followed by ``chmod`` would expose
    them through an existing 0644 kit. A symlinked kit is written through to the file it names, and
    the replacement keeps the old file's owner when this process may set it (root, under ``sudo -E``).

    ``flip_api.scripts.env_utils.write_env_file`` is the same routine for the two generator scripts
    that run inside flip-api's package and virtual environment. This module sits at the repository
    root and is imported by the plain ``make register-trust`` scripts, so it cannot import that one.

    Args:
        target (Path): The env file to write.
        lines (list[str]): Lines to write, without trailing newlines. Empty writes an empty file,
            not a lone newline: the seeding call passes ``[]`` when there is no example to copy,
            and a stray newline there becomes a leading blank line in the finished kit.

    Returns:
        None
    """
    # Write a 0600 temp file beside the kit and rename it over the kit, so an interrupted write leaves
    # the old kit whole: it may be the only plaintext copy of TRUST_API_KEY. The rename goes to the
    # file a symlinked kit names (an operator may keep the kit elsewhere behind a link), not over
    # the link, and the new file keeps the old one's owner where this process may set it.
    real = os.path.realpath(target)
    directory = os.path.dirname(real)
    try:
        previous = os.stat(real)
    except FileNotFoundError:
        previous = None
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            # mkstemp already creates 0600; set it explicitly so the mode never depends on that.
            os.fchmod(handle.fileno(), KIT_FILE_MODE)
            if previous is not None and (previous.st_uid, previous.st_gid) != (os.getuid(), os.getgid()):
                with contextlib.suppress(PermissionError):
                    os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
            # Guard the empty case so an empty list writes an empty file, not a lone "\n" that would
            # leave a leading blank line; the two copies of this routine must stay identical.
            handle.write("\n".join(lines) + "\n" if lines else "")
            handle.flush()
            os.fsync(handle.fileno())
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


def write_kit(target: Path, kit: dict, example: Path | None = None, hub_shared_commented: bool = False) -> None:
    """Merge a registration kit into ``target`` in place, preserving operator edits.

    Seeds ``target`` from ``example`` on first write (so the host-local profile
    is present), then upserts credentials (if the kit carries them), metadata,
    and the hub-shared block. Finally restricts the file to owner read/write.

    Args:
        target (Path): The kit file to write (e.g. ``trust/.env.GSTT.development``).
        kit (dict): A kit dict from ``register_trust`` — always has metadata and
            ``hub_shared``; has the credential fields only on a new registration.
        example (Path | None): Template to seed from when ``target`` is absent.
        hub_shared_commented (bool): Dev mode — write the hub-shared keys commented
            out (their values are inherited from the hub's .env.development, so a
            live copy would only drift). Default False writes them live (prod).

    Returns:
        None

    Raises:
        ValueError: If the kit sets a hub-shared key outside ``HUB_SHARED_KEYS``, or a value that is
            not one plain line. The kit comes from the hub and the file is read by make, so nothing is
            written.
    """
    hub_shared = kit.get("hub_shared") or {}
    unknown = sorted(set(hub_shared) - set(HUB_SHARED_KEYS))
    if unknown:
        raise ValueError(f"the kit sets hub-shared keys this checkout does not manage: {unknown!r}")
    managed = {env_key: kit.get(field) for env_key, field in _FIELD_BY_ENV_KEY.items()} | hub_shared
    unsafe = sorted(key for key, value in managed.items() if value is not None and unsafe_value(value))
    if unsafe:
        # The keys only: some of the values are credentials.
        raise ValueError(f"the kit's value for {', '.join(unsafe)} is not one line without `$`, backquote or backslash")

    if not target.exists():
        write_secure(target, (example.read_text() if example and example.exists() else "").splitlines())

    lines = target.read_text().splitlines()

    for env_key in (*CREDENTIAL_KEYS, *METADATA_KEYS):
        value = kit.get(_FIELD_BY_ENV_KEY[env_key])
        if value is not None:
            lines = upsert(lines, env_key, str(value))

    if hub_shared_commented:
        # Dev kit: document the hub-shared keys under the sentinel but keep them
        # inert (commented). Their values come from the hub's .env.development,
        # so a live copy here would only drift.
        if SENTINEL not in lines:
            lines.append("")
            lines.append(SENTINEL)
        if HUB_SHARED_DEV_NOTE not in lines:
            lines.insert(lines.index(SENTINEL) + 1, HUB_SHARED_DEV_NOTE)
        for key in HUB_SHARED_KEYS:
            lines = comment_out(lines, key)
    elif hub_shared:
        if SENTINEL not in lines:
            lines.append("")
            lines.append(SENTINEL)
        for key, value in hub_shared.items():
            lines = upsert(lines, key, str(value))

    write_secure(target, lines)
