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
from imaging_api.utils.xnat_url import XNAT_REQUEST_TIMEOUT, xnat_url

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
        scan_id (str): The ID of the scan in XNAT. Will be created if it does not exist.
        resource_id (str): XNAT resource type e.g DICOM/NIFTI. Custom value is allowed. Will be created if it does not
        exist.
        files_relative_paths_to_upload (list[str]): List of relative file paths to be uploaded.
        exist_ok (bool): Whether to overwrite the file if it already exists.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        list[str]: List of URLs of the uploaded files.

    Raises:
        FileNotFoundError: If any of the files to be uploaded are not found.
        ValueError: If the net ID or any file path attempts path traversal outside the upload directory.
        Exception: If the scan does not exist and the session holds no scan to take its type from.
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

    # The file PUT creates a missing resource but not a missing scan, so create only a missing scan, typed like
    # the session's first scan: XNAT refuses a scan PUT that would change an existing scan's type.
    scan_xsi_types = get_scan_xsi_types_from_experiment_response(experiment_response)
    if scan_id not in scan_xsi_types:
        if not scan_xsi_types:
            raise Exception(f"Cannot create scan {scan_id}: session {accession_id} holds no scan to take its type from")
        create_xnat_scan(
            project_id=project_id,
            subject_id=subject_id,
            experiment_id_or_label=accession_id,
            scan_id=scan_id,
            xsi_type=next(iter(scan_xsi_types.values())),
            headers=headers,
        )

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
    Creates a scan of the given xsi:type in XNAT.

    Args:
        project_id (str): The ID of the XNAT project.
        subject_id (str): The ID of the subject in XNAT.
        experiment_id_or_label (str): The ID or label of the experiment in XNAT.
        scan_id (str): The ID of the new scan.
        xsi_type (str): The new scan's xsi:type, e.g. ``xnat:crScanData``.
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

    response = requests.put(scan_url, headers=headers, timeout=XNAT_REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise Exception(f"Error creating scan: {response.text}")


def get_scan_xsi_types_from_experiment_response(experiment_response: dict[str, Any]) -> dict[str, str]:
    """
    Extracts each scan's xsi:type, by scan ID, from the XNAT experiment response JSON.

    Args:
        experiment_response (dict[str, Any]): XNAT experiment response JSON.

    Returns:
        dict[str, str]: Scan ID to xsi:type, e.g. ``{"1": "xnat:crScanData"}``, in the session's order.

    Raises:
        Exception: If there is an error during the parsing of the experiment response JSON.
    """
    try:
        return {
            str(scan["data_fields"]["ID"]): str(scan["meta"]["xsi:type"])
            for child in experiment_response["items"][0].get("children", [])
            if child["field"] == "scans/scan"
            for scan in child["items"]
        }
    except Exception as e:
        raise Exception(f"Failed to parse XNAT experiment data JSON: {str(e)}")


def xnat_kept_existing_file(response: requests.Response) -> bool:
    """
    Whether XNAT kept an existing file instead of writing the upload.

    Without ``overwrite=true``, XNAT keeps an existing file and still answers 200, naming it under ``duplicates``
    in a JSON body that it serves as ``text/html``.

    Args:
        response (requests.Response): XNAT's response to a file PUT.

    Returns:
        bool: True if the body lists a duplicate, False for a clean write (an empty or non-JSON body).
    """
    try:
        body = json.loads(response.content)
    except ValueError:
        return False
    return isinstance(body, dict) and bool(body.get("duplicates"))


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
        exist_ok (bool): Whether to overwrite the file if it already exists.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: The URL of the uploaded file.

    Raises:
        ValueError: If an identifier or filename is empty or a dot-segment.
        AlreadyExistsError: If XNAT kept a file with the same name in the specified resource instead of writing.
        Exception: If there is an error during the upload process.
    """
    file_name = Path(file_path).name

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
        query={"inbody": "true", "overwrite": "true"} if exist_ok else {"inbody": "true"},
    )

    # Upload the file. The PUT is its own existence check: see xnat_kept_existing_file.
    with open(file_path, "rb") as file:
        response = requests.put(url, headers=headers, data=file)

    # XNAT may answer a file PUT with 200 or 201, so accept any 2xx
    if not response.ok:
        raise Exception(f"Error uploading file {file_path}: {response.status_code} {response.text}")
    if xnat_kept_existing_file(response):
        raise AlreadyExistsError(f"File already exists on XNAT: {url}")

    logger.info(f"Successfully uploaded file: {file_path}")
    return url
