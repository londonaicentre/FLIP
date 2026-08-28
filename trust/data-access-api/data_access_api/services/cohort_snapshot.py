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

"""Durable, project-keyed store for the approved-cohort snapshot (FLIP#857).

At project approval the trust materialises its cohort dataframe once; this module persists
that artefact on the local filesystem (``COHORT_SNAPSHOT_DIR``, a dedicated bind mount) so
the row-level routes can serve a frozen, immutable cohort instead of re-running the SQL
against live OMOP on every call. Deliberately a file store: data-access-api keeps zero
write access to any database, and researcher SQL (pinned to the ``omop`` schema by
``validate_query``) cannot reach a filesystem at all.

Layout, one directory per hub project id::

    <COHORT_SNAPSHOT_DIR>/<project-uuid>/
        dataframe.parquet   # the frozen cohort, dtype-faithful (parquet, no index)
        meta.json           # row_count / columns / query_hash / created_at / format_version

Writes are atomic at directory granularity: everything lands in a ``.tmp-*`` sibling first
and is activated with ``os.replace`` renames, so a reader never observes a half-written
snapshot and a crash mid-write leaves (at worst) a stale temp directory that the boot-time
sweep removes. There is deliberately NO TTL and no in-place mutation — a snapshot is
immutable until it is overwritten by a re-approval or deleted; an approved cohort must not
silently vanish or drift mid-training (contrast ``services/query_cache.py``, the volatile
per-process cache this module's API is modelled on).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from data_access_api.config import get_settings
from data_access_api.utils.logger import logger

# Bumped when the on-disk layout changes; a snapshot with an unknown version is treated as
# absent (legacy live-SQL fall-through) rather than mis-read.
_FORMAT_VERSION = 1
_DATA_FILENAME = "dataframe.parquet"
_META_FILENAME = "meta.json"
# Work-in-progress / superseded directories. Never valid snapshots; swept at startup.
_TMP_PREFIX = ".tmp-"
_OLD_PREFIX = ".old-"


class SnapshotStoreDisabled(Exception):
    """Raised on writes when ``COHORT_SNAPSHOT_DIR`` is not configured."""


class SnapshotTooLarge(Exception):
    """Raised when the serialized snapshot exceeds ``SNAPSHOT_MAX_BYTES`` (never truncated)."""


@dataclass(frozen=True)
class SnapshotMeta:
    """The snapshot's serving-relevant facts, readable without deserialising the frame."""

    row_count: int
    columns: list[str]
    query_hash: str
    created_at: str  # ISO-8601 UTC
    format_version: int = _FORMAT_VERSION

    @property
    def has_accessions(self) -> bool:
        return "accession_id" in self.columns


@dataclass(frozen=True)
class Snapshot:
    df: pd.DataFrame
    meta: SnapshotMeta


def normalised_query_hash(query: str) -> str:
    """SHA-256 of the whitespace-normalised, lowercased SQL text.

    Used only to *detect and log* when a caller-supplied query differs from the one the
    snapshot froze — never as a security control (the frozen artefact is served either
    way; that is the point). Hashes the raw submitted text, not the validator's re-emitted
    form, so the hub-side string of record compares equal across submission and serving.
    """
    normalised = " ".join(query.strip().lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


def snapshot_enabled() -> bool:
    """Whether a snapshot directory is configured (empty ``COHORT_SNAPSHOT_DIR`` = disabled)."""
    return bool(get_settings().COHORT_SNAPSHOT_DIR)


def _store_dir() -> Path:
    configured = get_settings().COHORT_SNAPSHOT_DIR
    if not configured:
        raise SnapshotStoreDisabled("COHORT_SNAPSHOT_DIR is not configured")
    return Path(configured)


def _canonical_project_id(project_id: str) -> str | None:
    """The project id as a canonical UUID string, or None when it is not a UUID.

    The id becomes a directory name, so only a parsed-and-re-emitted UUID is ever used as
    a path component — nothing else reaches the filesystem (no traversal surface, even
    though every caller is already authenticated and the id is hub-encrypted).
    """
    try:
        return str(uuid.UUID(str(project_id)))
    except (ValueError, AttributeError, TypeError):
        return None


def ensure_store() -> None:
    """Boot-time store check: create the directory, sweep stale temp dirs, probe writability.

    Never raises — a missing or unwritable store must not take the service (and the
    statistics route) down. Failures log at ERROR with the remediation; every subsequent
    write fails loudly per-call and every read returns None, which the row-level routes
    refuse (fail-closed).
    """
    if not snapshot_enabled():
        logger.error(
            "Cohort snapshot store DISABLED (COHORT_SNAPSHOT_DIR unset): snapshots cannot be "
            "created and the row-level routes will refuse every project (fail-closed). Set "
            "COHORT_SNAPSHOT_DIR / mount the snapshot volume."
        )
        return

    base = _store_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        # Sweep leftovers from crashed writes: only this service writes here, and no write
        # can be in flight during startup.
        for stale in base.iterdir():
            if stale.name.startswith((_TMP_PREFIX, _OLD_PREFIX)):
                shutil.rmtree(stale, ignore_errors=True)
                logger.warning(f"Removed stale snapshot work directory {stale.name}")
        probe = base / f"{_TMP_PREFIX}write-probe"
        probe.mkdir(exist_ok=True)
        probe.rmdir()
    except OSError:
        logger.exception(
            f"Cohort snapshot store at {base} is not writable — snapshots cannot be created and "
            "the row-level routes will refuse projects whose artefact cannot be read (fail-closed). "
            f"Remediation: create the directory on the host and chown it to this service's uid "
            f"(uid {os.getuid()})."
        )
        return

    logger.info(f"Cohort snapshot store ready at {base}")


def save_snapshot(project_id: str, df: pd.DataFrame, query_hash: str) -> SnapshotMeta:
    """Persist the cohort dataframe for ``project_id``, atomically replacing any predecessor.

    Args:
        project_id (str): The decrypted hub project id (must be a UUID).
        df (pd.DataFrame): The cohort exactly as ``get_records`` returned it.
        query_hash (str): ``normalised_query_hash`` of the raw SQL that produced ``df``.

    Returns:
        SnapshotMeta: What was written.

    Raises:
        SnapshotStoreDisabled: When no store directory is configured.
        SnapshotTooLarge: When the serialized frame exceeds ``SNAPSHOT_MAX_BYTES``.
        ValueError: When ``project_id`` is not a UUID.
        OSError: When the store directory is not writable.
    """
    base = _store_dir()
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        raise ValueError("project_id must be a UUID to key a cohort snapshot")

    # Parquet keeps pandas dtypes (datetimes, nullable ints) so every training-time fetch of
    # the frozen cohort deserialises to an identical frame. The index is dropped — the serve
    # routes emit to_dict(orient="list"), which never includes it.
    buffer = io.BytesIO()
    df.to_parquet(buffer, engine="pyarrow", index=False)
    payload = buffer.getvalue()

    max_bytes = get_settings().SNAPSHOT_MAX_BYTES
    if len(payload) > max_bytes:
        # Refuse rather than truncate: a partial cohort silently poisons training data.
        raise SnapshotTooLarge(
            f"Serialized cohort snapshot is {len(payload)} bytes, over the {max_bytes}-byte limit "
            "(SNAPSHOT_MAX_BYTES). Narrow the cohort query's columns, or raise the limit."
        )

    meta = SnapshotMeta(
        row_count=len(df),
        columns=[str(column) for column in df.columns],
        query_hash=query_hash,
        created_at=datetime.now(UTC).isoformat(),
    )

    base.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:8]
    workdir = base / f"{_TMP_PREFIX}{canonical}-{nonce}"
    final = base / canonical
    superseded = base / f"{_OLD_PREFIX}{canonical}-{nonce}"
    try:
        workdir.mkdir()
        (workdir / _DATA_FILENAME).write_bytes(payload)
        (workdir / _META_FILENAME).write_text(json.dumps(meta.__dict__))

        # Two atomic renames. A crash between them leaves no active snapshot (readers fall
        # back to live SQL and the boot sweep clears the debris) — never a half-written one.
        if final.exists():
            os.replace(final, superseded)
        os.replace(workdir, final)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(superseded, ignore_errors=True)

    logger.info(
        f"Cohort snapshot saved for project {canonical}: {meta.row_count} rows, "
        f"{len(meta.columns)} columns, {len(payload)} bytes"
    )
    return meta


def get_snapshot(project_id: str) -> Snapshot | None:
    """The frozen cohort for ``project_id``, or None when there is none to serve.

    None covers every no-artefact case — store disabled, non-UUID project id, no snapshot
    yet, unreadable/corrupt artefact (logged at ERROR). The row-level routes refuse on
    None (fail-closed); a corrupt artefact is never partially served.
    """
    if not snapshot_enabled():
        return None
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        logger.debug(f"Project id {project_id!r} is not a UUID; no snapshot lookup")
        return None

    snapshot_dir = _store_dir() / canonical
    meta_path = snapshot_dir / _META_FILENAME
    if not meta_path.exists():
        return None

    try:
        raw_meta = json.loads(meta_path.read_text())
        meta = SnapshotMeta(**raw_meta)
        if meta.format_version != _FORMAT_VERSION:
            logger.error(
                f"Cohort snapshot for project {canonical} has format_version "
                f"{meta.format_version} (expected {_FORMAT_VERSION}) — treating as absent"
            )
            return None
        df = pd.read_parquet(snapshot_dir / _DATA_FILENAME, engine="pyarrow")
    except Exception:
        logger.exception(
            f"Cohort snapshot for project {canonical} is unreadable — treating as absent "
            "(row-level routes refuse the project rather than serve a partial artefact)"
        )
        return None

    return Snapshot(df=df, meta=meta)


def delete_snapshot(project_id: str) -> bool:
    """Remove the snapshot for ``project_id``. Idempotent; True if one existed."""
    if not snapshot_enabled():
        return False
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        return False

    snapshot_dir = _store_dir() / canonical
    if not snapshot_dir.exists():
        return False
    # Move aside first so a concurrent reader sees either the intact snapshot or none —
    # never a directory whose files are vanishing under it mid-read.
    tomb = _store_dir() / f"{_OLD_PREFIX}{canonical}-{uuid.uuid4().hex[:8]}"
    os.replace(snapshot_dir, tomb)
    shutil.rmtree(tomb, ignore_errors=True)
    logger.info(f"Cohort snapshot deleted for project {canonical}")
    return True
