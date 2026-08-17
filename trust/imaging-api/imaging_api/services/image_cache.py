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

A download of (project, accession, assessor_type, resource_type) is marked complete by an
empty dotfile inside the accession directory, written only after extraction succeeds:

    <BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/
        .flip_complete-<assessor_type>-<resource_type>

The sentinel — not the presence of image files — is the cache key: extraction merges into
the directory member-by-member, so a partially-extracted folder can hold plausible-looking
files. Sentinels are exact-match per (assessor_type, resource_type); ``ALL`` and a specific
resource type cache independently. Helpers are pure functions over explicit directory
arguments so callers keep patching their own module-level ``BASE_IMAGES_DOWNLOAD_DIR``.
"""

import glob
import os
from pathlib import Path

SENTINEL_PREFIX = ".flip_complete-"


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
        resource_type (str): XNAT resource type e.g. DICOM/NIFTI/ALL, or a custom value.

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


def write_sentinel(accession_dir_abs: str, assessor_type: str, resource_type: str) -> None:
    """
    Marks a download of (assessor, resource) into the accession directory as complete.

    Creates the directory if extraction produced nothing (a zip with no members still counts
    as a completed download — there is nothing more XNAT would return for it).

    Args:
        accession_dir_abs (str): Absolute accession directory the download extracted into.
        assessor_type (str): The assessor type of the completed download.
        resource_type (str): The resource type of the completed download.
    """
    os.makedirs(accession_dir_abs, exist_ok=True)
    Path(sentinel_path(accession_dir_abs, assessor_type, resource_type)).touch()


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
