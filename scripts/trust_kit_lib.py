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

One in-place-upsert implementation behind every operator-side kit write — the
dev distributor, the prod distributor, and the hub-shared sync. Merges a kit
dict (as emitted by ``flip_api.scripts.register_trust``) into a kit file while
preserving the operator's host-local edits:

- Credentials (``TRUST_API_KEY`` / ``TRUST_INTERNAL_SERVICE_KEY`` /
  ``TRUST_AES_KEY_BASE64`` / ``TRUST_AES_KID``) are written only when present in
  the kit (a new registration). The idempotent skip path omits them, so an
  existing kit's credentials are never clobbered (the hub stores only hashes,
  and does not retain the AES key at all, so neither can be re-emitted).
- Metadata (``EXPECTED_TRUST_ID`` / ``FL_KIT_SLOT`` / ``FL_KIT_SLOT_NUMBER``)
  is present on both paths and upserted unconditionally.
- The hub-shared block is upserted under a sentinel header; the header is added
  once on first write.

Replaces the bash upsert in scripts/distribute-trust-kits.sh and
deploy/providers/AWS/scripts/register-trusts.sh::write_kit_file(), and the
in-place upsert in scripts/sync_trust_kit.py.
"""

from __future__ import annotations

import os
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
# flip_api/scripts/register_trust.py:HUB_SHARED_ENV_KEYS (the emitter, which
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
    "DOCKER_REGISTRY",
    "DOCKER_FL_TAG",
    "DOCKER_FL_REGISTRY",
    "NLB_SUBDOMAIN",
    "FL_SERVER_PORT",
)

# Plaintext credentials — written once on a new registration, never on skip.
CREDENTIAL_KEYS: tuple[str, ...] = (
    "TRUST_API_KEY",
    "TRUST_INTERNAL_SERVICE_KEY",
    "TRUST_AES_KEY_BASE64",
)

# Written commented-out, with its value preserved. Setting TRUST_AES_KID live is what
# switches a trust onto its own key, and doing that before the hub holds the same key
# breaks both directions: the trust encrypts to the hub under a kid the hub cannot
# resolve, and the hub encrypts to the trust under a kid the trust has not loaded.
# The kit therefore ships the value ready to use but inert — see
# docs/aes-payload-keys.md for the enablement order.
COMMENTED_CREDENTIAL_KEYS: tuple[str, ...] = ("TRUST_AES_KID",)

# Metadata — present on both the new-registration and idempotent-skip paths.
METADATA_KEYS: tuple[str, ...] = ("EXPECTED_TRUST_ID", "FL_KIT_SLOT", "FL_KIT_SLOT_NUMBER")

# Maps each managed env key to the field name in the kit dict it is sourced from.
_FIELD_BY_ENV_KEY: dict[str, str] = {
    "TRUST_API_KEY": "trust_api_key",
    "TRUST_INTERNAL_SERVICE_KEY": "trust_internal_service_key",
    "TRUST_AES_KEY_BASE64": "trust_aes_key",
    "TRUST_AES_KID": "trust_aes_kid",
    "EXPECTED_TRUST_ID": "trust_id",
    "FL_KIT_SLOT": "fl_kit_slot",
    "FL_KIT_SLOT_NUMBER": "fl_kit_slot_number",
}


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    """Replace the first ``KEY=...`` line in place, or append ``KEY=value``.

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


def upsert_commented(lines: list[str], key: str, value: str) -> list[str]:
    """Upsert ``# KEY=value`` — the value is delivered, but inert until uncommented.

    Distinct from :func:`comment_out`, which discards the value. Here the operator
    needs the value in the file to enable it later, so it must survive; and distinct
    from :func:`upsert`, which would make it live immediately.

    An existing *live* ``KEY=`` line is left alone: an operator who has deliberately
    enabled the key must not have it silently switched off by a re-registration.

    Args:
        lines (list[str]): Current file lines (no trailing newlines).
        key (str): Env var name to write commented.
        value (str): Value to preserve behind the comment.

    Returns:
        list[str]: A new list of lines with the key present as ``# KEY=value``.
    """
    if any(not ln.lstrip().startswith("#") and ln.split("=", 1)[0] == key for ln in lines):
        return lines

    out: list[str] = []
    replaced = False
    for line in lines:
        bare = line.lstrip("#").lstrip()
        if not replaced and line.lstrip().startswith("#") and bare.split("=", 1)[0] == key:
            out.append(f"# {key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"# {key}={value}")
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
    """Write ``lines`` to ``target``, restricting it to 0600 before any content lands.

    Do not use ``Path.write_text`` followed by ``chmod``: ``write_text`` does not change the mode of
    an existing file, so a kit file already at 0644 — hand-created, or seeded under a stock umask 022
    — holds the freshly minted ``TRUST_API_KEY`` and ``TRUST_INTERNAL_SERVICE_KEY`` world-readable
    for the window between the two calls, and permanently if the process is interrupted between
    them. Opening without ``O_TRUNC`` lets ``fchmod`` run first; truncate and write only once the
    descriptor is 0600.

    ``flip_api.scripts.env_utils.write_env_file`` is the same routine for the two generator scripts
    that run inside flip-api's package and virtual environment. This module sits at the repository
    root and is imported by the plain ``make register-trust`` scripts, so it cannot import that one.

    Args:
        target (Path): The env file to write.
        lines (list[str]): Lines to write, without trailing newlines. Empty writes an empty file,
            not a lone newline — the seeding call passes ``[]`` when there is no example to copy,
            and a stray newline there becomes a leading blank line in the finished kit.

    Returns:
        None
    """
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT, KIT_FILE_MODE), "w") as handle:
        os.fchmod(handle.fileno(), KIT_FILE_MODE)
        handle.truncate(0)
        # Guard the empty case so an empty list writes an empty file, not a lone "\n" that would
        # leave a leading blank line — matching flip_api.scripts.env_utils.write_env_file exactly.
        handle.write("\n".join(lines) + "\n" if lines else "")


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
    """
    if not target.exists():
        write_secure(target, (example.read_text() if example and example.exists() else "").splitlines())

    lines = target.read_text().splitlines()

    for env_key in (*CREDENTIAL_KEYS, *METADATA_KEYS):
        value = kit.get(_FIELD_BY_ENV_KEY[env_key])
        if value is not None:
            lines = upsert(lines, env_key, str(value))

    for env_key in COMMENTED_CREDENTIAL_KEYS:
        value = kit.get(_FIELD_BY_ENV_KEY[env_key])
        if value is not None:
            lines = upsert_commented(lines, env_key, str(value))

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
    else:
        hub_shared = kit.get("hub_shared") or {}
        if hub_shared:
            if SENTINEL not in lines:
                lines.append("")
                lines.append(SENTINEL)
            for key, value in hub_shared.items():
                lines = upsert(lines, key, str(value))

    write_secure(target, lines)
