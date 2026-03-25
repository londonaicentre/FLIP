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
from pathlib import Path

import requests
from fastapi import APIRouter

from imaging_api.config import get_settings
from imaging_api.services.projects import (
    get_experiment,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
)
from imaging_api.utils.exceptions import AlreadyExistsError
from imaging_api.utils.logger import logger

XNAT_URL = get_settings().XNAT_URL
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
    """
    # Get base directory from configuration
    upload_directory = os.path.join(BASE_IMAGES_DOWNLOAD_DIR, net_id, "upload")

    # Verify and build full file paths
    full_file_paths: list[str] = []
    for file_relative_path in files_relative_paths_to_upload:
        full_path = os.path.join(upload_directory, file_relative_path)
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

    # Create scan in XNAT
    # Note this function will not complain if the scan already exists.
    create_xnat_scan(
        project_id=project_id,
        subject_id=subject_id,
        experiment_id_or_label=accession_id,
        scan_id=scan_id,
        headers=headers,
    )

    # Create resource in XNAT
    try:
        create_xnat_resource(
            project_id=project_id,
            subject_id=subject_id,
            experiment_id_or_label=accession_id,
            scan_id=scan_id,
            resource_id=resource_id,
            headers=headers,
        )
    except AlreadyExistsError:
        # This is the only exception we want to ignore
        logger.info(f"Resource '{resource_id}' already exists for this scan. Skipping creation.")

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

    return uploaded_files


def create_xnat_scan(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    scan_id: str,
    headers: dict[str, str],
) -> None:
    """
    Creates a scan in XNAT if it does not exist.

    Normally when we upload a file to XNAT, the scan will already exist.
    TODO reassess if this function is necessary -- could require the scan to exist

    In order to create a scan, you must specify the xsiType of the scan.
    See https://wiki.xnat.org/xnat-api/image-session-scans-api#ImageSessionScansAPI-AddScanToAnImageSession
    TODO Note this is hardcoded to MR scans below (xsiType=xnat:mrScanData), but it is not really used for anything.

    Note this function will not complain if the scan already exists.

    Args:
        project_id (str): The ID of the XNAT project.
        subject_id (str): The ID of the subject in XNAT.
        experiment_id_or_label (str): The ID or label of the experiment in XNAT.
        scan_id (str): The ID of the scan in XNAT.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        None

    Raises:
        Exception: If there is an error during the creation of the scan.
    """
    scan_url = (
        f"{XNAT_URL}/data/projects/{project_id}/subjects/{subject_id}/"
        f"experiments/{experiment_id_or_label}/scans/{scan_id}"
        f"?xsiType=xnat:mrScanData"
    )

    response = requests.put(scan_url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Error creating scan: {response.text}")


def create_xnat_resource(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    scan_id: str,
    resource_id: str,
    headers: dict[str, str],
) -> None:
    """
    Creates a resource folder under a scan in XNAT if it does not exist.
    A resource can have any name, but it is typically "DICOM" or "NIFTI".

    Args:
        project_id (str): The ID of the XNAT project.
        subject_id (str): The ID of the subject in XNAT.
        experiment_id_or_label (str): The ID or label of the experiment in XNAT.
        scan_id (str): The ID of the scan in XNAT.
        resource_id (str): The ID of the resource in XNAT.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        None

    Raises:
        AlreadyExistsError: If the resource already exists in XNAT.
        Exception: If there is an error during the creation of the resource.
    """
    resource_url = (
        f"{XNAT_URL}/data/projects/{project_id}/subjects/{subject_id}/"
        f"experiments/{experiment_id_or_label}/scans/{scan_id}/"
        f"resources/{resource_id}"
    )

    response = requests.put(resource_url, headers=headers)

    if response.status_code == 409:
        raise AlreadyExistsError(f"Resource already exists: {resource_id}")
    elif response.status_code != 200:
        raise Exception(f"Error creating resource: {response.text}")


def check_file_exists_in_xnat(check_url: str, headers: dict[str, str]) -> bool:
    """
    Checks if a file already exists in XNAT.

    Args:
        check_url (str): The URL to check for the file.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        bool: True if the file exists, False otherwise.
    """
    response = requests.get(check_url, headers=headers)
    return response.status_code == 200


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
        AlreadyExistsError: If exist_ok is False and a file with the same name already exists in the specified XNAT
        resource.
        Exception: If there is an error during the upload process.
    """
    file_name = Path(file_path).name

    url = (
        f"{XNAT_URL}/data/projects/{project_id}/subjects/{subject_id}/"
        f"experiments/{experiment_id_or_label}/scans/{scan_id}/"
        f"resources/{resource_id}/files/{file_name}?inbody=true"
    )

    # Check if the file already exists if exist_ok is False
    # If exist_ok is True, we will overwrite the file if it exists
    if not exist_ok:
        if check_file_exists_in_xnat(url, headers):
            raise AlreadyExistsError(f"File already exists on XNAT: {url}")

    # Upload the file
    with open(file_path, "rb") as file:
        response = requests.put(url, headers=headers, data=file)
        logger.info(f"Successfully uploaded file: {file_path}")
        return url

    if response.status_code != 200:
        raise Exception(f"Error uploading file {file_path}: {response.text}")
