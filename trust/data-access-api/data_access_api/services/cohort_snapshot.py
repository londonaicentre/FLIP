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

"""Durable, project-keyed store for the approved-cohort MEMBERSHIP (FLIP#857).

At project approval the trust runs its cohort query once and records who is in it: the query of
record plus the ``person_id`` and/or ``accession_id`` values it returned. The row-level routes then
re-run that stored query (never the caller's SQL) and keep only rows whose ids are in the frozen
set, so an approved cohort can SHRINK — a patient removed from OMOP (an opt-out, a correction) drops
out and their data is no longer reachable — but can never GROW with the live database.

What is stored is identifiers and the SQL text, never clinical values: the artefact is a small,
human-readable JSON file an operator can inspect, diff and audit, and the attribute data stays in
OMOP where the trust's existing governance applies to it. Deliberately a file store: data-access-api
keeps zero write access to any database, and researcher SQL (pinned to the ``omop`` schema by
``validate_query``) cannot reach a filesystem at all.

Layout, one file per hub project id::

    <COHORT_SNAPSHOT_DIR>/<project-uuid>/membership.json

Writes are atomic at directory granularity and write-once: the file lands in a ``.tmp-*`` sibling
first and is activated with a single ``os.rename`` onto the project's directory, which the kernel
refuses when a record is already there. So a reader never observes a half-written record, a crash
mid-write leaves (at worst) a stale temp directory that the boot-time sweep removes, and no write —
a re-queued snapshot, two racing ones — can replace a frozen membership. There is no TTL and no
in-place mutation: a record is written once and then only deleted.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from data_access_api.config import get_settings
from data_access_api.utils.logger import logger

# Bumped when the on-disk layout changes. A record with an unknown version is never mis-read: the
# row-level routes refuse the project, and a freeze request answers an error rather than writing over it.
_FORMAT_VERSION = 2
_MEMBERSHIP_FILENAME = "membership.json"
# Work-in-progress directories. Never valid records; swept at startup.
_TMP_PREFIX = ".tmp-"
# A record being deleted. Swept at startup: the deletion wins.
_DEL_PREFIX = ".del-"
# The boot sweep leaves work directories younger than this alone. A write takes well under a
# second, so anything this old was abandoned by a crash; anything younger may belong to another
# replica writing to the same (ReadWriteMany) store right now.
_SWEEP_MIN_AGE_SECONDS = 600


class SnapshotStoreDisabled(Exception):
    """Raised on writes when ``COHORT_SNAPSHOT_DIR`` is not configured."""


class SnapshotTooLarge(Exception):
    """Raised when the serialized record exceeds ``SNAPSHOT_MAX_BYTES`` (never truncated)."""


class SnapshotExists(Exception):
    """Raised when a write finds a membership already frozen for the project (records are write-once)."""


class SnapshotUnreadable(Exception):
    """Raised when a record is present but cannot be read: corrupt, an unknown format, or an I/O error.

    Distinct from "no record" on purpose. Serving refuses either way, but a freeze must never treat an
    unreadable record as absent: it would re-run the query and write today's cohort over the approved one.
    """


@dataclass(frozen=True)
class Snapshot:
    """A project's frozen cohort membership.

    Validated on construction, so a record read back from disk that is malformed — above all one
    that freezes no id column, which would leave the serving filter nothing to restrict by — raises
    and is treated as absent rather than served. The sequences are stored as tuples, so the frozen
    record cannot be mutated in memory.
    """

    # The raw SQL of record, re-run (through validate_query) on every row-level fetch.
    query: str
    query_hash: str
    # The frozen member ids, as strings. None when the cohort does not project that column; empty
    # when it does but every value was NULL. Serving keeps a row only when every frozen column it
    # has a value in holds a member, and at least one does, so neither a new patient nor a new study
    # of an existing patient can enter an approved cohort.
    person_ids: Sequence[str] | None
    accession_ids: Sequence[str] | None
    # Facts at approval, for the hub's audit strip and drift check. Serving re-counts live.
    row_count: int
    subject_count: int
    columns: Sequence[str]
    created_at: str  # ISO-8601 UTC
    format_version: int = _FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("a cohort membership needs its query of record")
        if self.person_ids is None and self.accession_ids is None:
            raise ValueError("a cohort membership must freeze person_ids, accession_ids or both")
        for name in ("person_ids", "accession_ids", "columns"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, str) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} must be a list of strings")
            object.__setattr__(self, name, tuple(value))
        for name in ("row_count", "subject_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def has_accessions(self) -> bool:
        """Whether the cohort froze any accession id — False for a tabular cohort, and for one whose
        ``accession_id`` column was NULL on every row: neither has imaging to pull."""
        return bool(self.accession_ids)


def normalised_query_hash(query: str) -> str:
    """SHA-256 of the whitespace-normalised, lowercased SQL text.

    Used only to *detect and log* when a caller-supplied query differs from the one of record —
    never as a security control (the stored query is served either way; that is the point).
    Hashes the raw submitted text, not the validator's re-emitted form, so the hub-side string of
    record compares equal across submission and serving.
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
    write fails loudly per-call, so no new project can be frozen, and a project whose record
    cannot be read is refused by the row-level routes (fail-closed).
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
        # Sweep leftovers from crashed writes and deletes — only those old enough that no writer
        # can still own them (see _SWEEP_MIN_AGE_SECONDS).
        cutoff = time.time() - _SWEEP_MIN_AGE_SECONDS
        for stale in sorted(base.iterdir(), key=lambda path: path.name):
            if not stale.name.startswith((_TMP_PREFIX, _DEL_PREFIX)):
                continue
            try:
                if stale.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(stale)
            except FileNotFoundError:
                continue  # another replica finished with it first
            logger.warning(f"Removed stale snapshot work directory {stale.name}")
        probe = base / f"{_TMP_PREFIX}write-probe-{uuid.uuid4().hex[:8]}"
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


def save_snapshot(
    project_id: str,
    query: str,
    person_ids: Sequence[str] | None,
    accession_ids: Sequence[str] | None,
    row_count: int,
    subject_count: int,
    columns: Sequence[str],
) -> Snapshot:
    """Persist the cohort membership for ``project_id``, once: an existing record is never replaced.

    Args:
        project_id (str): The decrypted hub project id (must be a UUID).
        query (str): The raw SQL of record.
        person_ids (Sequence[str] | None): The frozen ``person_id`` values, or None when not projected.
        accession_ids (Sequence[str] | None): The frozen ``accession_id`` values, or None when not projected.
        row_count (int): Rows the query returned at approval.
        subject_count (int): Distinct subjects at approval, as ``count_distinct_subjects`` took them.
        columns (Sequence[str]): The query's column names at approval.

    Returns:
        Snapshot: What was written.

    Raises:
        SnapshotStoreDisabled: When no store directory is configured.
        SnapshotTooLarge: When the serialized record exceeds ``SNAPSHOT_MAX_BYTES``.
        SnapshotExists: When a membership is already frozen for the project.
        ValueError: When ``project_id`` is not a UUID, or the membership is malformed (no id column).
        OSError: When the store directory is not writable.
    """
    base = _store_dir()
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        raise ValueError("project_id must be a UUID to key a cohort snapshot")

    snapshot = Snapshot(
        query=query,
        query_hash=normalised_query_hash(query),
        person_ids=person_ids,
        accession_ids=accession_ids,
        row_count=row_count,
        subject_count=subject_count,
        columns=columns,
        created_at=datetime.now(UTC).isoformat(),
    )
    payload = json.dumps(asdict(snapshot), indent=1).encode()

    max_bytes = get_settings().SNAPSHOT_MAX_BYTES
    if len(payload) > max_bytes:
        # Refuse rather than truncate: a partial membership silently drops patients from training.
        raise SnapshotTooLarge(
            f"Serialized cohort membership is {len(payload)} bytes, over the {max_bytes}-byte limit "
            "(SNAPSHOT_MAX_BYTES). Raise the limit."
        )

    base.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:8]
    workdir = base / f"{_TMP_PREFIX}{canonical}-{nonce}"
    final = base / canonical
    try:
        workdir.mkdir()
        with open(workdir / _MEMBERSHIP_FILENAME, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        # One atomic rename. A directory renames over an EMPTY directory but never a non-empty one
        # (ENOTEMPTY/EEXIST), so the check that no record exists and the write are a single step:
        # a reader sees no record or the whole one, and two racing writers cannot both land.
        try:
            os.rename(workdir, final)
        except OSError as err:
            if err.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise SnapshotExists(f"a cohort membership is already frozen for project {canonical}") from err
            raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    logger.info(
        f"Cohort membership saved for project {canonical}: {len(person_ids or [])} person ids, "
        f"{len(accession_ids or [])} accession ids, {len(payload)} bytes"
    )
    return snapshot


def load_snapshot(project_id: str) -> Snapshot | None:
    """The frozen membership for ``project_id``, or None ONLY when no record exists.

    The strict read the freeze uses: a record that is present but cannot be read raises, so it is
    never mistaken for "not frozen yet".

    Args:
        project_id (str): The decrypted hub project id.

    Returns:
        Snapshot | None: The record, or None when the store is disabled, the id is not a UUID, or
        no record exists.

    Raises:
        SnapshotUnreadable: When a record exists but is corrupt, of an unknown format version, or
            cannot be read (an I/O or permission error).
    """
    if not snapshot_enabled():
        return None
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        logger.debug(f"Project id {project_id!r} is not a UUID; no snapshot lookup")
        return None

    try:
        text = (_store_dir() / canonical / _MEMBERSHIP_FILENAME).read_text()
    except FileNotFoundError:
        return None
    except OSError as err:
        raise SnapshotUnreadable(f"cohort membership for project {canonical} cannot be read: {err}") from err
    try:
        raw = json.loads(text)
        if raw.get("format_version") != _FORMAT_VERSION:
            raise SnapshotUnreadable(
                f"cohort membership for project {canonical} has format_version "
                f"{raw.get('format_version')} (expected {_FORMAT_VERSION})"
            )
        return Snapshot(**raw)
    except SnapshotUnreadable:
        raise
    except Exception as err:
        raise SnapshotUnreadable(f"cohort membership for project {canonical} is malformed: {err}") from err


def get_snapshot(project_id: str) -> Snapshot | None:
    """The frozen membership for ``project_id``, or None when there is none to serve.

    The lenient read the row-level routes use: None covers every no-record case — store disabled,
    non-UUID project id, not approved yet — and an unreadable record (logged at ERROR). The routes
    refuse on None (fail-closed).
    """
    try:
        return load_snapshot(project_id)
    except SnapshotUnreadable:
        logger.exception("Cohort membership is unreadable — refusing the project's row-level routes")
        return None


def delete_snapshot(project_id: str) -> bool:
    """Remove the snapshot for ``project_id``. Idempotent; True if one existed."""
    if not snapshot_enabled():
        return False
    canonical = _canonical_project_id(project_id)
    if canonical is None:
        return False

    base = _store_dir()
    snapshot_dir = base / canonical
    if not snapshot_dir.exists():
        return False
    # Move aside first so a concurrent reader sees either the intact snapshot or none —
    # never a directory whose files are vanishing under it mid-read.
    tomb = base / f"{_DEL_PREFIX}{canonical}-{uuid.uuid4().hex[:8]}"
    os.replace(snapshot_dir, tomb)
    shutil.rmtree(tomb, ignore_errors=True)
    logger.info(f"Cohort snapshot deleted for project {canonical}")
    return True
