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

import json
import os
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter

from imaging_api.config import get_settings
from imaging_api.services.image_cache import invalidate_accession
from imaging_api.services.projects import (
    get_experiment,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
)
from imaging_api.utils.exceptions import AlreadyExistsError
from imaging_api.utils.logger import logger
from imaging_api.utils.xnat_url import xnat_url

BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR

# Create router
router = APIRouter(prefix="/upload", tags=["Upload"])


async def upload_data_to_xnat(
    central_hub_project_id: str,
    accession_id: str,
    net_id: str,
    scan_id: str,
    resource_id: str,
    files_relative_paths_to_upload: list[str],
    exist_ok: bool,
    headers: dict[str, str],
) -> list[str]:
    """
    Uploads image files to XNAT under a specific project, experiment, and scan.

    Args:
        central_hub_project_id (str): The central hub project ID in which the experiment belongs to. Corresponds to
        XNAT secondary ID.
        accession_id (str): The unique value for a study stored in PACS. Corresponds to XNAT experiment label.
        net_id (str): The ID of the FL net that will run the training.
        scan_id (str): The ID of the scan in XNAT. Will be created if it does not exist, with the scan type that
        matches the session (see ``xsi_type_for_new_scan``).
        resource_id (str): XNAT resource type e.g DICOM/NIFTI. Custom value is allowed. XNAT creates it with the first
        file if it does not exist.
        files_relative_paths_to_upload (list[str]): List of relative file paths to be uploaded.
        exist_ok (bool): Whether to replace a file of the same name that already exists in the resource.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[str]: List of URLs of the uploaded files.

    Raises:
        FileNotFoundError: If any of the files to be uploaded are not found.
        ValueError: If the net ID or any file path attempts path traversal outside the upload directory.
        AlreadyExistsError: If exist_ok is False and XNAT reports that a file of the same name already exists.
    """
    # Get base directory from configuration
    upload_directory = os.path.join(BASE_IMAGES_DOWNLOAD_DIR, net_id, "upload")
    base_images_download_dir_abs = os.path.realpath(BASE_IMAGES_DOWNLOAD_DIR)
    upload_directory_abs = os.path.realpath(upload_directory)

    if not upload_directory_abs.startswith(base_images_download_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in net ID: {net_id}")

    # Verify and build full file paths, rejecting any path traversal attempts
    full_file_paths: list[str] = []
    for file_relative_path in files_relative_paths_to_upload:
        full_path = os.path.realpath(os.path.join(upload_directory_abs, file_relative_path))
        if not full_path.startswith(upload_directory_abs + os.sep):
            raise ValueError(f"Path traversal detected in file path: {file_relative_path}")
        full_file_paths.append(full_path)

    # Check files exist
    for file_path in full_file_paths:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

    # Get project ID from central hub project ID
    project = get_project_from_central_hub_project_id(central_hub_project_id, headers)
    project_id = project.ID

    # Get experiment details
    experiment_response = get_experiment(
        project_id=project_id,
        experiment_id_or_label=accession_id,
        headers=headers,
    )

    # Parse subject ID from experiment response
    subject_id = get_subject_id_from_experiment_response(experiment_response)

    # A file PUT needs its scan to exist: XNAT answers 404 "Unable to identify scan" otherwise
    # (xnat-web 1.10.0 XNATTemplate.java:268-270). So only a scan the session does not hold yet is
    # created, typed like the session. An existing scan is left alone, since a scan PUT carrying
    # another xsiType is refused with a 409 (ScanResource.java:294-297).
    session_xsi_type, existing_scan_xsi_types = get_xsi_types_from_experiment_response(experiment_response)
    if scan_id not in existing_scan_xsi_types:
        create_xnat_scan(
            project_id=project_id,
            subject_id=subject_id,
            experiment_id_or_label=accession_id,
            scan_id=scan_id,
            xsi_type=xsi_type_for_new_scan(session_xsi_type, list(existing_scan_xsi_types.values())),
            headers=headers,
        )

    # There is no resource PUT: the file PUT creates the resource, with this label, when the scan
    # has none (xnat-web 1.10.0 ResourceModifierA.addFile, lines 106-117).

    # Upload files to XNAT
    uploaded_files: list[str] = []
    for file_path in full_file_paths:
        uploaded_file = upload_file_to_xnat(
            project_id=project_id,
            subject_id=subject_id,
            experiment_id_or_label=accession_id,
            scan_id=scan_id,
            resource_id=resource_id,
            file_path=file_path,
            exist_ok=exist_ok,
            headers=headers,
        )
        uploaded_files.append(uploaded_file)

    # The upload changed this accession's content in XNAT, so every net's cached download of
    # (project, accession) is stale — drop the completeness sentinels so the next request
    # re-downloads. Best-effort: the XNAT upload already succeeded, and failing here would
    # push the client into retrying a completed upload; a swallowed error only costs one
    # extra (harmless, merging) re-download later.
    try:
        removed = invalidate_accession(BASE_IMAGES_DOWNLOAD_DIR, central_hub_project_id, accession_id)
        if removed:
            logger.info(
                f"Invalidated {removed} cached download sentinel(s) for "
                f"project={central_hub_project_id} accession={accession_id} after upload"
            )
    except (ValueError, OSError) as e:
        logger.warning(f"Could not invalidate download-cache sentinels for accession {accession_id}: {e}")

    return uploaded_files


def create_xnat_scan(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    scan_id: str,
    xsi_type: str,
    headers: dict[str, str],
) -> None:
    """
    Creates a new scan in an XNAT session.

    XNAT needs the scan's xsi:type to create it, and refuses a scan PUT without one with a 417 "Need PUT Contents".
    Call this only for a scan ID the session does not hold yet. On an existing scan, XNAT refuses a PUT whose xsiType
    differs from the scan's own with a 409 "Specified xsiType differs from existing xsiType" (xnat-web 1.10.0
    ScanResource.java:294-297).

    Args:
        project_id (str): The ID of the XNAT project.
        subject_id (str): The ID of the subject in XNAT.
        experiment_id_or_label (str): The ID or label of the experiment in XNAT.
        scan_id (str): The ID of the new scan.
        xsi_type (str): The new scan's xsi:type, e.g. ``xnat:crScanData`` (see ``xsi_type_for_new_scan``).
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        None

    Raises:
        ValueError: If an identifier is empty or a dot-segment.
        Exception: If there is an error during the creation of the scan.
    """
    scan_url = xnat_url(
        "data",
        "projects",
        project_id,
        "subjects",
        subject_id,
        "experiments",
        experiment_id_or_label,
        "scans",
        scan_id,
        query={"xsiType": xsi_type},
    )

    response = requests.put(scan_url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Error creating scan: {response.text}")


def get_xsi_types_from_experiment_response(experiment_response: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """
    Extracts the session's xsi:type and the xsi:type of each scan it holds from the XNAT experiment response JSON.

    The response is XNAT's full experiment document (``GET .../experiments/{label}?format=json``). It lists the
    session's scans as its ``scans/scan`` child, each with its own ``meta["xsi:type"]`` and ``data_fields["ID"]``.

    Args:
        experiment_response (dict[str, Any]): XNAT experiment response JSON.

    Returns:
        tuple[str, dict[str, str]]: The session's xsi:type (e.g. ``xnat:crSessionData``), and each existing scan's ID
        mapped to its xsi:type (e.g. ``{"1": "xnat:crScanData"}``), in the session's order.

    Raises:
        Exception: If the response JSON does not have the shape of an XNAT experiment document.
    """
    try:
        item = experiment_response["items"][0]
        session_xsi_type = str(item["meta"]["xsi:type"])
        scan_xsi_types = {
            str(scan["data_fields"]["ID"]): str(scan["meta"]["xsi:type"])
            for child in item.get("children", [])
            if child.get("field") == "scans/scan"
            for scan in child.get("items", [])
        }
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise Exception(f"Failed to parse XNAT experiment data JSON: {e!r}")
    return session_xsi_type, scan_xsi_types


def xsi_type_for_new_scan(session_xsi_type: str, existing_scan_xsi_types: list[str]) -> str:
    """
    Chooses the xsi:type of a scan that FLIP adds to an existing XNAT session.

    XNAT keeps no session-to-scan type table. Its DICOM importer types each scan and each session from the SOP class
    of the images (dicom-xnat-sop ``SOPModel``, with ``series-scans.properties`` and ``study-sessions.properties``).
    Those two tables pair every ``xnat:<m>SessionData`` they produce with ``xnat:<m>ScanData``, with two exceptions:
    an ``xnat:rtSessionData`` holds ``xnat:rtImageScanData``, and an ``xnat:otherDicomSessionData`` holds other types.
    A PET-MR session holds both PET and MR scans. So the new scan takes, in order:

    1. the session's own scan type, ``xnat:<m>ScanData``, if the session already holds a scan of that type;
    2. otherwise the type of the session's first scan, which XNAT chose when it imported that scan;
    3. with no scan to copy, the session's own scan type, by name.

    Args:
        session_xsi_type (str): The session's xsi:type, e.g. ``xnat:crSessionData``.
        existing_scan_xsi_types (list[str]): The xsi:types of the session's existing scans, in the session's order.

    Returns:
        str: The xsi:type for the new scan, e.g. ``xnat:crScanData``.

    Raises:
        ValueError: If the session holds no scan and its xsi:type is not of the form ``<prefix>SessionData``.
    """
    own_scan_xsi_type = (
        session_xsi_type.removesuffix("SessionData") + "ScanData" if session_xsi_type.endswith("SessionData") else None
    )
    if own_scan_xsi_type is not None and own_scan_xsi_type in existing_scan_xsi_types:
        return own_scan_xsi_type
    if existing_scan_xsi_types:
        return existing_scan_xsi_types[0]
    if own_scan_xsi_type is None:
        raise ValueError(f"Cannot choose a scan type for a new scan in an empty session of type {session_xsi_type!r}")
    return own_scan_xsi_type


def xnat_upload_duplicates(response: requests.Response) -> list[str]:
    """
    Returns the files XNAT reports it kept instead of writing, from a file PUT's response body.

    Without ``overwrite=true``, XNAT does not replace a file that already exists: it keeps the old one
    and still answers HTTP 200, with ``{"duplicates": [...]}`` as the body. A clean write answers 200
    with an empty body. (xnat-web 1.10.0, ``CatalogUtils.java:1764`` and ``FileList.java:345-349``.)

    XNAT serves the ``duplicates`` body as ``text/html``, so the body is parsed as JSON whatever the
    Content-Type says. An empty or non-JSON body means nothing was reported.

    Args:
        response (requests.Response): XNAT's response to a file PUT.

    Returns:
        list[str]: The duplicate file paths XNAT reported, or an empty list if the file was written.
    """
    try:
        body = json.loads(response.content)
    except ValueError:
        return []
    duplicates = body.get("duplicates") if isinstance(body, dict) else None
    return [str(duplicate) for duplicate in duplicates] if isinstance(duplicates, list) else []


def upload_file_to_xnat(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    scan_id: str,
    resource_id: str,
    file_path: str,
    exist_ok: bool,
    headers: dict[str, str],
) -> str:
    """
    Uploads a file to the specified resource in XNAT.

    Args:
        project_id (str): The ID of the XNAT project.
        subject_id (str): The ID of the subject in XNAT.
        experiment_id_or_label (str): The ID or label of the experiment in XNAT.
        scan_id (str): The ID of the scan in XNAT.
        resource_id (str): The ID of the resource in XNAT, for example "DICOM" or "NIFTI".
        file_path (str): The path to the file to be uploaded.
        exist_ok (bool): Whether to replace a file of the same name that already exists. True sends XNAT's
        ``overwrite=true``; False sends no flag, so XNAT keeps an existing file and reports it as a duplicate.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: The URL of the uploaded file.

    Raises:
        ValueError: If an identifier or filename is empty or a dot-segment.
        AlreadyExistsError: If exist_ok is False and XNAT reports that a file with the same name already exists in
        the specified resource. XNAT then keeps the existing file and writes nothing.
        Exception: If there is an error during the upload process, or if exist_ok is True but XNAT still kept the
        existing file.
    """
    file_name = Path(file_path).name

    query = {"inbody": "true"}
    if exist_ok:
        # XNAT replaces an existing file only when the request asks it to. A bare PUT keeps the old
        # file and still answers 200 (xnat-web 1.10.0 FileList.java:295, CatalogUtils.java:1764).
        query["overwrite"] = "true"

    url = xnat_url(
        "data",
        "projects",
        project_id,
        "subjects",
        subject_id,
        "experiments",
        experiment_id_or_label,
        "scans",
        scan_id,
        "resources",
        resource_id,
        "files",
        file_name,
        query=query,
    )

    # Upload the file. There is no existence pre-check: the PUT itself reports a file it did not
    # write, so asking first would only open a window for another writer between the two requests.
    with open(file_path, "rb") as file:
        response = requests.put(url, headers=headers, data=file)

    # XNAT may answer a file PUT with 200 or 201, so accept any 2xx
    if not response.ok:
        raise Exception(f"Error uploading file {file_path}: {response.status_code} {response.text}")

    # A 200 is not proof of a write: XNAT keeps an existing file and lists it under "duplicates".
    duplicates = xnat_upload_duplicates(response)
    if duplicates:
        if not exist_ok:
            raise AlreadyExistsError(f"File already exists on XNAT: {url}")
        raise Exception(f"XNAT kept the existing file instead of replacing it: {url} (duplicates: {duplicates})")

    logger.info(f"Successfully uploaded file: {file_path}")
    return url
