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

import os
import zipfile
from pathlib import PurePosixPath

import requests

from imaging_api.config import get_settings
from imaging_api.services.image_cache import (
    CachedDownload,
    clear_resource,
    read_sentinel,
    sentinel_path,
    write_sentinel,
)
from imaging_api.services.projects import (
    get_experiment,
    get_experiment_field,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
)
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError, XnatFetchError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import xnat_url

# Get download directory
BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR

# The resource-listing columns a cache hit compares: scan id, label, resource id, file count, bytes.
_STATS_COLUMNS = ("cat_id", "label", "xnat_abstractresource_id", "file_count", "file_size")

# Short: a cache hit holds imaging-api's single event loop while it waits for this listing.
_CACHE_CHECK_TIMEOUT_SECONDS = 5


async def download_and_unzip_images(
    central_hub_project_id: str,
    accession_id: str,
    net_id: str,
    assessor_type: str,
    resource_type: str,
    headers: dict[str, str],
    force_refresh: bool = False,
) -> str:
    """
    Downloads XNAT experiment image data corresponding to a given accession ID and encrypted central hub project id.

    If data exists for the experiment, all image scan resources will be downloaded and the location returned.

    All data is downloaded in a single .zip file. Once the file download is complete, this method will then attempt
    to unzip the folder and delete the existing .zip file.

    Downloads are cached on disk per (net, project, accession): extraction lands in
    ``<BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/`` and a completeness
    sentinel per (assessor_type, resource_type) is written there after successful extraction. When the
    sentinel is present the cached folder is returned unless XNAT's resource stats show a change (see the
    README, "Download") — FL training code calls
    this once per accession per round, and without the cache each of those calls is a full study
    re-download of bytes already on local disk (FLIP#953). The per-project path segment keeps projects
    that share an accession from being served each other's copies (their XNAT content can differ, e.g.
    per-project label enrichment).

    Args:
        central_hub_project_id (str): Central Hub project ID. Corresponds to XNAT secondary ID.
        accession_id (str): The unique value for a study stored in PACS. Corresponds to XNAT experiment label.
        net_id (str): The ID of the FL net that will run the training.
        assessor_type (str): The type of assessor to use for the download ("scan" or "assessor").
        resource_type (str): XNAT resource type, one of DICOM, NIFTI, SEG or ALL (the router's ``ResourceType``
        Literal; custom values are rejected there). ALL will download all resources.
        headers (dict[str, str]): XNAT authentication headers.
        force_refresh (bool): Re-download even when a completed cached copy exists. The resource's cached
        files and covering sentinels are deleted first, once XNAT has answered, and rewritten on success.

    Returns:
        str: The path to the unzipped folder containing the downloaded images.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project with the given ID is not found, if no experiments are
        found for the given accession ID, if no data is found at the download URL, or if the ZIP file is not found after
        download.
        ValueError: If the net ID, central hub project ID, accession ID, assessor type, or resource type attempts
        path traversal outside its directory level.
        Exception: If there is an error during any of the requests to XNAT, during the download process, or during the
        unzipping process.
    """
    # Concurrency invariant: this coroutine contains no `await`, and imaging-api runs one uvicorn
    # worker (`fastapi run`/`fastapi dev`, never --workers — the entrypoint.sh command carries the
    # matching warning), so concurrent calls for the same key serialise end-to-end and cannot race
    # on the shared zip path or its extraction below. Introducing an await here, extra workers, or a
    # threadpool offload voids that and needs an explicit per-(net, project, accession) lock — or
    # download-to-temp-dir-then-rename — instead. FLIP#1026 tracks the temp-dir staging; this
    # comment (and the entrypoint one) go when it lands.
    # Validate net_id doesn't escape base images directory
    download_dir = os.path.join(BASE_IMAGES_DOWNLOAD_DIR, net_id)
    base_images_download_dir_abs = os.path.realpath(BASE_IMAGES_DOWNLOAD_DIR)
    download_dir_abs = os.path.realpath(download_dir)
    if not download_dir_abs.startswith(base_images_download_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in net ID: {net_id}")

    # The cache path segments are user-controlled too; guard each level the same way.
    project_dir_abs = os.path.realpath(os.path.join(download_dir_abs, central_hub_project_id))
    if not project_dir_abs.startswith(download_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in central hub project ID: {central_hub_project_id!r}")
    accession_dir_abs = os.path.realpath(os.path.join(project_dir_abs, accession_id))
    if not accession_dir_abs.startswith(project_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in accession_id: {accession_id!r}")

    # Cache check. The sentinel (written only after a fully successful extraction) is the completeness
    # marker — the mere presence of image files is not trusted, because extraction merges member-by-member
    # and a crash can leave a partial folder. A hit is checked against XNAT (see the README, "Download").
    context = (
        f"net={net_id} project={central_hub_project_id} accession={accession_id} ({assessor_type}/{resource_type})"
    )
    cached = not force_refresh and os.path.isfile(sentinel_path(accession_dir_abs, assessor_type, resource_type))
    record = read_sentinel(accession_dir_abs, assessor_type, resource_type) if cached else None
    stale = "force_refresh" if force_refresh else "its sentinel predates the XNAT record" if cached else None
    if record is not None:
        if record.resource_stats is None:  # an assessor download: XNAT has no assessor resource listing
            logger.info(f"Cache hit for {context}: reusing {accession_dir_abs} (not checked against XNAT)")
            return accession_dir_abs
        try:
            current = get_resource_stats(record.experiment_id, resource_type, headers)
        except NotFoundError:
            current = None
        except (requests.RequestException, XnatFetchError) as e:
            return _serve_unchecked(accession_dir_abs, context, e)
        if current == record.resource_stats:
            logger.info(f"Cache hit for {context}: reusing {accession_dir_abs} (unchanged in XNAT)")
            return accession_dir_abs
        stale = f"XNAT stats {record.resource_stats} -> {current}"

    # Ensure the per-net/per-project download directory exists before we try to
    # write into it. A missing net_id subdir is a deployment/misconfiguration
    # issue (the bind mount was never provisioned for this net), not a "remote
    # resource not found" — surface it as LocalStorageError so the router returns 500.
    try:
        os.makedirs(project_dir_abs, exist_ok=True)
    except OSError as e:
        raise LocalStorageError(
            f"Cannot create image download directory {project_dir_abs!r} on the trust host: {e}"
        ) from e

    try:
        project_id, subject_id, experiment_id, resource_stats = _look_up(
            central_hub_project_id, accession_id, assessor_type, resource_type, headers
        )
    except NotFoundError:
        if stale:  # the study is gone from XNAT, so its cached copy goes too
            _clear(accession_dir_abs, assessor_type, resource_type, context, f"{stale}; gone from XNAT")
        raise
    except Exception as e:
        if cached and record is None:  # an older sentinel: nothing to check it by, so keep serving it
            return _serve_unchecked(accession_dir_abs, context, e)
        raise
    if stale:
        _clear(accession_dir_abs, assessor_type, resource_type, context, stale)

    # Format download URL
    download_url = format_download_url(
        project_id=project_id,
        subject_id=subject_id,
        experiment_id_or_label=accession_id,
        assessor_type=assessor_type,
        resource_type=resource_type,
    )
    logger.info(f"Download URL: {download_url}")

    # Define download and extraction paths. net_id and the project segment were
    # validated above; we still have to guard the final zip path because
    # accession_id and resource_type are user-controlled and could smuggle `..`
    # segments that would escape project_dir_abs into a sibling directory or out
    # of BASE_IMAGES_DOWNLOAD_DIR entirely.
    zip_file_path = os.path.join(project_dir_abs, f"{accession_id}-scans-{resource_type}.zip")
    zip_file_path_abs = os.path.realpath(zip_file_path)
    if not zip_file_path_abs.startswith(project_dir_abs + os.sep):
        raise ValueError(
            f"Path traversal detected in accession_id or resource_type: "
            f"accession_id={accession_id!r} resource_type={resource_type!r}"
        )
    zip_file_path = zip_file_path_abs

    # Download the ZIP file
    try:
        downloaded_file = download_file(download_url, zip_file_path, headers)
    except NotFoundError as e:
        # XNAT genuinely has no data at this URL — the cohort entry is orphaned.
        raise NotFoundError(f"No DICOM data in XNAT at {download_url}: {e.detail}")
    except LocalStorageError:
        # Let local-write failures bubble through with their original (accurate)
        # message and status; do not re-wrap them as "not found" — that's what
        # masked the real cause in the prior incident.
        raise
    except Exception as e:
        raise Exception(f"Failed to download file from {download_url}: {str(e)}")
    logger.info(f"Downloaded file: {downloaded_file}")

    # The zip is rooted at the session label, i.e. the accession id, so it extracts into accession_dir_abs.
    extracted_folder = unzip_file(downloaded_file, project_dir_abs, accession_id)
    logger.debug(f"Extracted folder: {extracted_folder}")

    # List contents of the extracted folder recursively
    for root, dirs, files in os.walk(extracted_folder):
        for name in files:
            logger.debug(f"File: {os.path.join(root, name)}")
        for name in dirs:
            logger.debug(f"Directory: {os.path.join(root, name)}")

    # Only now — after a fully successful extraction — mark this (assessor, resource)
    # download complete, with the record a later hit is checked by.
    write_sentinel(accession_dir_abs, assessor_type, resource_type, CachedDownload(experiment_id, resource_stats))
    logger.info(
        f"Marked download complete for net={net_id} project={central_hub_project_id} "
        f"accession={accession_id} ({assessor_type}/{resource_type})"
    )

    return extracted_folder


def _look_up(
    central_hub_project_id: str, accession_id: str, assessor_type: str, resource_type: str, headers: dict[str, str]
) -> tuple[str, str, str, list[list[str]] | None]:
    """
    Finds the accession's XNAT project, subject and experiment, then reads its resource stats.

    The stats are read before the zip, so a change landing in between shows on the next hit.

    Returns:
        tuple[str, str, str, list[list[str]] | None]: Project ID, subject ID, experiment ID, and the
        stats (None for an assessor download).
    """
    # Get project ID from central hub project ID
    try:
        project = get_project_from_central_hub_project_id(central_hub_project_id, headers)
        project_id = project.ID
    except NotFoundError as e:
        raise NotFoundError(f"Project with ID {central_hub_project_id} not found: {str(e)}")
    except Exception as e:
        raise Exception(f"Failed to fetch project ID: {str(e)}")
    logger.info(f"Project ID: {project_id}")

    # Get experiment details
    try:
        experiment_response = get_experiment(
            project_id=project_id,
            experiment_id_or_label=accession_id,
            headers=headers,
        )
    except NotFoundError as e:
        raise NotFoundError(f"Experiment with ID or label {accession_id} not found: {str(e)}")
    except Exception as e:
        raise Exception(f"Failed to fetch experiment details: {str(e)}")

    experiment_id = get_experiment_field(experiment_response, "ID")
    stats = get_resource_stats(experiment_id, resource_type, headers) if assessor_type.lower() == "scan" else None
    return project_id, get_subject_id_from_experiment_response(experiment_response), experiment_id, stats


def _serve_unchecked(accession_dir_abs: str, context: str, error: Exception) -> str:
    """Serves a cached copy that XNAT could not be asked about, with a warning."""
    logger.warning(
        f"Cache hit for {context}: could not check XNAT for changes ({type(error).__name__}: {error}); "
        f"serving {accession_dir_abs} unchecked"
    )
    return accession_dir_abs


def _clear(accession_dir_abs: str, assessor_type: str, resource_type: str, context: str, reason: str) -> None:
    """Deletes a resource's stale cached files before it is downloaded again."""
    logger.info(f"Cached copy for {context} is stale ({reason}); deleting it before downloading again")
    try:
        clear_resource(accession_dir_abs, assessor_type, resource_type)
    except OSError as e:
        raise LocalStorageError(f"Cannot delete the stale cached copy in {accession_dir_abs}: {e}") from e


def get_resource_stats(experiment_id: str, resource_type: str, headers: dict[str, str]) -> list[list[str]]:
    """
    Fetches the ``_STATS_COLUMNS`` rows of the scan resources a download of ``resource_type`` covers.

    ``ALL`` keeps every row; any other type keeps the rows labelled exactly that, as XNAT's lookup does.
    Counts and sizes rather than file digests: XNAT fills ``digest`` only with its ``checksums`` setting on.

    Args:
        experiment_id (str): XNAT's experiment ID (``XNAT_E…``).
        resource_type (str): The download's resource type: DICOM, NIFTI, SEG or ALL.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[list[str]]: The rows, sorted.

    Raises:
        ValueError: If the experiment ID is empty or a dot-segment.
        imaging_api.utils.exceptions.NotFoundError: If XNAT has no experiment with that ID.
        imaging_api.utils.exceptions.XnatFetchError: For any other non-200, or a body that is not a listing.
        requests.RequestException: If the request fails or times out.
    """
    url = xnat_url("data", "experiments", experiment_id, "scans", "ALL", "resources", query={"format": "json"})
    response = requests.get(url, headers=headers, timeout=_CACHE_CHECK_TIMEOUT_SECONDS)
    if response.status_code == 404:
        raise NotFoundError(f"XNAT experiment {experiment_id} not found")
    if response.status_code != 200:
        raise XnatFetchError(f"XNAT resource listing failed: {response.status_code} - {response.text[:200]}")
    try:
        rows = response.json()["ResultSet"]["Result"]
        wanted = rows if resource_type.upper() == "ALL" else [r for r in rows if r.get("label") == resource_type]
        return sorted([str(row.get(column)) for column in _STATS_COLUMNS] for row in wanted)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise XnatFetchError(f"XNAT resource listing for {experiment_id} is not a ResultSet: {e}") from e


def format_download_url(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    assessor_type: str = "scan",
    resource_type: str = "NIFTI",
) -> str:
    """
    Formats the XNAT API URL to download experiment scan images.

    Identifiers are quoted at this boundary even when a caller bypasses request validation.
    ``structure=default`` pins the zip layout the cache and the FL apps read (see the README, "Download").

    Args:
        project_id (str): XNAT project ID.
        subject_id (str): XNAT subject ID.
        experiment_id_or_label (str): XNAT experiment ID or its label.
        assessor_type (str): Type of assessor (scan or assessor).
        resource_type (str): Resource type (e.g. `NIFTI`, `DICOM`, etc.).

    Returns:
        str: Formatted URL for downloading images.

    Raises:
        ValueError: If an identifier is empty or a dot-segment.
    """
    assert assessor_type.lower() in [
        "scan",
        "assessor",
    ], "Type must be 'scan' or 'assessor'"
    segments = (
        "data",
        "projects",
        project_id,
        "subjects",
        subject_id,
        "experiments",
        experiment_id_or_label,
        f"{assessor_type.lower()}s",
        "ALL",
    )
    # resource_type=ALL means "every resource on the scan". XNAT has no literal ALL
    # resource label — /resources/ALL/files 404s — so drop the resources segment and
    # let XNAT return files across all resource labels. This also covers DICOM series
    # whose resource XNAT labels other than "DICOM" (e.g. "secondary" for
    # Secondary Capture SOP classes, as produced by synthetic datasets).
    query = {"format": "zip", "structure": "default"}
    if resource_type.upper() == "ALL":
        return xnat_url(*segments, "files", query=query)
    return xnat_url(*segments, "resources", resource_type, "files", query=query)


def download_file(url: str, destination_path: str, headers: dict[str, str]) -> str:
    """
    Downloads a file from the given URL using an XNAT auth headers.

    Args:
        url (str): URL to download the file from.
        destination_path (str): Path to save the downloaded file.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: Path to the downloaded file.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If XNAT has no data at the given URL (remote 404).
        imaging_api.utils.exceptions.LocalStorageError: If the local filesystem cannot accept the
            downloaded file (missing/unwritable destination directory, disk full, etc.).
        Exception: If there is an upstream/transport error during the download request.
    """
    response = requests.get(url, headers=headers, stream=True)

    if response.status_code == 404:
        raise NotFoundError(f"No data found at: {url}")

    if response.status_code != 200:
        raise Exception(f"Error: Failed to download file: {response.status_code} - {response.text}")

    # Ensure the parent directory exists. `download_and_unzip_images` also
    # pre-creates the net_id dir, but we keep this as a defense-in-depth so
    # callers of `download_file` directly (tests, future code paths) still
    # work without surprise.
    parent_dir = os.path.dirname(destination_path)
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except OSError as e:
        raise LocalStorageError(f"Cannot create parent directory {parent_dir!r} for downloaded file: {e}") from e

    try:
        with open(destination_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024):
                file.write(chunk)
    except OSError as e:
        # Permission denied, disk full, bind mount stale, etc. These are all
        # trust-host problems — not "the remote URL is missing".
        raise LocalStorageError(f"Failed to write downloaded file to {destination_path!r}: {e}") from e

    return destination_path


def unzip_file(zip_path: str, extract_dir: str, expected_root: str) -> str:
    """
    Extracts an XNAT zip whose members all sit under ``expected_root/`` (see the README, "Download").

    Args:
        zip_path (str): Path to the ZIP file.
        extract_dir (str): Directory to extract the contents.
        expected_root (str): The folder every member must sit under (the accession id).

    Returns:
        str: Path to ``<extract_dir>/<expected_root>``.

    Raises:
        FileNotFoundError: If the ZIP file does not exist.
        ValueError: If expected_root, or a member through an on-disk symlink, escapes extract_dir.
        imaging_api.utils.exceptions.XnatFetchError: If the zip holds no files or a member outside
            ``expected_root/``. Nothing is extracted.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    extract_dir_abs = os.path.realpath(extract_dir)
    root_abs = os.path.realpath(os.path.join(extract_dir_abs, expected_root))
    if not root_abs.startswith(extract_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in expected root: {expected_root}")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        # Lexical on purpose: zipfile drops ".." parts rather than resolving them, so "X/../ACC/f" lands in X/ACC/f.
        names = zip_ref.namelist()
        stray = [n for n in names if not n.startswith(f"{expected_root}/") or ".." in PurePosixPath(n).parts]
        if stray or all(n.endswith("/") for n in names):
            raise XnatFetchError(f"XNAT zip must hold files under {expected_root}/ only; stray members: {stray[:3]}")

        # Validate each member and extract immediately within the same loop
        # iteration so CodeQL's taint-flow analysis can see that extraction
        # is gated on the realpath boundary check (py/zipslip).
        for member in names:
            member_path = os.path.realpath(os.path.join(extract_dir_abs, member))
            if not member_path.startswith(extract_dir_abs + os.sep):
                raise ValueError(f"Attempted path traversal in ZIP entry: {member}")
            zip_ref.extract(member, extract_dir_abs)

    # Delete the original ZIP file
    os.remove(zip_path)

    return root_abs
