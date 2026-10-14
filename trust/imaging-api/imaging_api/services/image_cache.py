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

"""Completeness sentinels for the on-disk image download cache.

A download of (project, accession, assessor_type, resource_type) is marked complete by a
dotfile inside the accession directory, written only after extraction succeeds:

    <BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/
        .flip_complete-<assessor_type>-<resource_type>

The sentinel — not the presence of image files — is the cache key: extraction merges into
the directory member-by-member, so a partially-extracted folder can hold plausible-looking
files. Sentinels are exact-match per (assessor_type, resource_type); ``ALL`` and a specific
resource type cache independently. Helpers are pure functions over explicit directory
arguments so callers keep patching their own module-level ``BASE_IMAGES_DOWNLOAD_DIR``.

The sentinel holds a small JSON record (``CachedDownload``) of what the download came from:
XNAT's experiment ID and the resource stats XNAT reported for it. A cache hit compares those
stats with XNAT's current ones, so an edit made straight in XNAT is seen. An empty or
unreadable sentinel, such as one written by an older imaging-api, reads as ``None``.
"""

import glob
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENTINEL_PREFIX = ".flip_complete-"
SENTINEL_VERSION = 1


@dataclass(frozen=True)
class CachedDownload:
    """
    What a completeness sentinel records about the download it marks.

    Attributes:
        experiment_id (str): XNAT's experiment ID (``XNAT_E…``) the copy came from. It stays fixed
            when a session is relabelled, so a hit can be checked with one call and no project or
            label lookup.
        resource_stats (list[list[str]] | None): XNAT's stats for the resources the download
            covered, one ``[scan id, label, resource id, file count, file size]`` row each, sorted.
            None when XNAT has no listing to compare against (assessor downloads).
    """

    experiment_id: str
    resource_stats: list[list[str]] | None


def _validate_component(name: str, value: str) -> None:
    """
    Rejects values that could act as path components rather than literal name fragments.

    Args:
        name (str): Human-readable parameter name for the error message.
        value (str): The user-controlled value to validate.

    Raises:
        ValueError: If the value is empty, a dot directory, or contains a path separator.
    """
    seps = [sep for sep in (os.sep, os.altsep) if sep]
    if not value or value in (".", "..") or any(sep in value for sep in seps):
        raise ValueError(f"Path traversal detected in {name}: {value!r}")


def sentinel_path(accession_dir_abs: str, assessor_type: str, resource_type: str) -> str:
    """
    Builds the absolute path of the completeness sentinel for one (assessor, resource) pair.

    Args:
        accession_dir_abs (str): Absolute (realpath'd) accession directory the sentinel lives in.
        assessor_type (str): The assessor type of the download ("scan" or "assessor").
        resource_type (str): XNAT resource type, one of DICOM, NIFTI, SEG or ALL.

    Returns:
        str: Absolute (resolved) path of the sentinel file, always a direct child of the accession
        directory.

    Raises:
        ValueError: If assessor_type or resource_type contains a path separator — both are
        user-controlled and must stay literal fragments of the sentinel filename — or if the
        resolved path escapes the accession directory.
    """
    _validate_component("assessor_type", assessor_type)
    _validate_component("resource_type", resource_type)
    # Second layer: even if component validation were bypassed, the resolved path must stay
    # inside the accession directory before any caller touches the filesystem with it.
    accession_dir_real = os.path.realpath(accession_dir_abs)
    sentinel_abs = os.path.realpath(
        os.path.join(accession_dir_real, f"{SENTINEL_PREFIX}{assessor_type}-{resource_type}")
    )
    if not sentinel_abs.startswith(accession_dir_real + os.sep):
        raise ValueError(f"Path traversal detected in sentinel name: {assessor_type!r}-{resource_type!r}")
    return sentinel_abs


def write_sentinel(accession_dir_abs: str, assessor_type: str, resource_type: str, record: CachedDownload) -> None:
    """
    Marks a download of (assessor, resource) into the accession directory as complete.

    A missing directory means the download extracted nothing, which is never marked complete.

    The write is not atomic and does not need to be: a crash part-way leaves an empty or
    truncated file, which ``read_sentinel`` reads as ``None``, so the next request downloads again.

    Args:
        accession_dir_abs (str): Absolute accession directory the download extracted into.
        assessor_type (str): The assessor type of the completed download.
        resource_type (str): The resource type of the completed download.
        record (CachedDownload): The XNAT experiment and resource stats the download came from.

    Raises:
        FileNotFoundError: If the accession directory does not exist.
    """
    if not os.path.isdir(accession_dir_abs):
        raise FileNotFoundError(f"{accession_dir_abs} does not exist; refusing to mark the download complete")
    payload = {
        "version": SENTINEL_VERSION,
        "experiment_id": record.experiment_id,
        "resource_stats": record.resource_stats,
    }
    Path(sentinel_path(accession_dir_abs, assessor_type, resource_type)).write_text(json.dumps(payload))


def _is_stats(value: Any) -> bool:
    """True if value is a list of lists of strings, the shape ``CachedDownload.resource_stats`` takes."""
    return isinstance(value, list) and all(
        isinstance(row, list) and all(isinstance(cell, str) for cell in row) for row in value
    )


def read_sentinel(accession_dir_abs: str, assessor_type: str, resource_type: str) -> CachedDownload | None:
    """
    Reads the record a completeness sentinel holds.

    Args:
        accession_dir_abs (str): Absolute accession directory the sentinel lives in.
        assessor_type (str): The assessor type of the download.
        resource_type (str): The resource type of the download.

    Returns:
        CachedDownload | None: The record. None when the sentinel is missing, empty (written by an
        imaging-api that predates the record), truncated, or not a record of this version.
    """
    try:
        payload = json.loads(Path(sentinel_path(accession_dir_abs, assessor_type, resource_type)).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != SENTINEL_VERSION:
        return None
    experiment_id = payload.get("experiment_id")
    stats = payload.get("resource_stats")
    if not isinstance(experiment_id, str) or not (stats is None or _is_stats(stats)):
        return None
    try:
        # The ID goes into an XNAT URL path; anything but a single plain segment is not a record.
        _validate_component("experiment_id", experiment_id)
    except ValueError:
        return None
    return CachedDownload(experiment_id=experiment_id, resource_stats=stats)


def clear_accession_dir(accession_dir_abs: str) -> None:
    """
    Deletes one cached accession directory: its images and every sentinel in it.

    Used when XNAT's copy of the study has changed. Extraction merges, so re-downloading over the
    old copy would keep the files XNAT has since deleted (#1026). Every (assessor, resource)
    download of the accession extracts into this one directory, so all its sentinels go with it.
    A sibling download then fetches again on its next request rather than being served from a
    half-replaced folder. A missing directory, or a file that vanishes mid-way (a concurrent
    NVFLARE ``CleanupImages``), is not an error.

    Args:
        accession_dir_abs (str): Absolute (realpath'd) accession directory, already checked by the
            caller to sit inside its project directory.

    Raises:
        OSError: If a file cannot be removed for any reason other than already being gone.
    """

    def _ignore_missing(function: Any, path: str, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            raise exc

    shutil.rmtree(accession_dir_abs, onexc=_ignore_missing)


def remove_sentinel(accession_dir_abs: str, assessor_type: str, resource_type: str) -> None:
    """
    Removes one completeness sentinel; a no-op if it does not exist.

    Args:
        accession_dir_abs (str): Absolute accession directory the sentinel lives in.
        assessor_type (str): The assessor type of the sentinel to remove.
        resource_type (str): The resource type of the sentinel to remove.
    """
    Path(sentinel_path(accession_dir_abs, assessor_type, resource_type)).unlink(missing_ok=True)


def invalidate_accession(base_dir: str, central_hub_project_id: str, accession_id: str) -> int:
    """
    Removes every completeness sentinel for (project, accession) across all net directories.

    Called after an upload changes the accession's content in XNAT: every net's cached copy of
    that (project, accession) is stale, so each must re-download on next request. Image content
    is never touched — only sentinel files are unlinked.

    Args:
        base_dir (str): The base images download directory holding one subdirectory per net.
        central_hub_project_id (str): Plaintext Central Hub project ID (the per-project cache
        path segment).
        accession_id (str): The accession whose sentinels to remove.

    Returns:
        int: Number of sentinel files removed (0 when nothing was cached).

    Raises:
        ValueError: If the project or accession id is empty, a dot directory, or contains a
        path separator that could redirect the glob outside per-accession dirs.
    """
    _validate_component("central hub project ID", central_hub_project_id)
    _validate_component("accession_id", accession_id)

    base_dir_abs = os.path.realpath(base_dir)
    pattern = os.path.join(
        base_dir_abs, "*", glob.escape(central_hub_project_id), glob.escape(accession_id), f"{SENTINEL_PREFIX}*"
    )
    removed = 0
    for match in glob.glob(pattern):
        match_abs = os.path.realpath(match)
        # Belt-and-braces: never unlink outside the base dir or a non-sentinel file.
        if not match_abs.startswith(base_dir_abs + os.sep):
            continue
        if not os.path.basename(match_abs).startswith(SENTINEL_PREFIX):
            continue
        try:
            os.unlink(match_abs)
            removed += 1
        except FileNotFoundError:
            continue
    return removed
