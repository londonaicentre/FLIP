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
from pathlib import Path

import requests

from imaging_api.config import get_settings
from imaging_api.services.projects import (
    get_experiment,
    get_project_from_central_hub_project_id,
    get_subject_id_from_experiment_response,
)
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError
from imaging_api.utils.logger import logger

# Get download directory
XNAT_URL = get_settings().XNAT_URL
BASE_IMAGES_DOWNLOAD_DIR = get_settings().BASE_IMAGES_DOWNLOAD_DIR


async def download_and_unzip_images(
    central_hub_project_id: str,
    accession_id: str,
    net_id: str,
    assessor_type: str,
    resource_type: str,
    headers: dict[str, str],
) -> str:
    """
    Downloads XNAT experiment image data corresponding to a given accession ID and encrypted central hub project id.

    If data exists for the experiment, all image scan resources will be downloaded and the location returned.

    All data is downloaded in a single .zip file. Once the file download is complete, this method will then attempt
    to unzip the folder and delete the existing .zip file.

    Args:
        central_hub_project_id (str): Central Hub project ID. Corresponds to XNAT secondary ID.
        accession_id (str): The unique value for a study stored in PACS. Corresponds to XNAT experiment label.
        net_id (str): The ID of the FL net that will run the training.
        assessor_type (str): The type of assessor to use for the download ("scan" or "assessor").
        resource_type (str): XNAT resource type e.g DICOM/NIFTI. ALL will download all resources. Custom value is
        allowed if researcher has added their own custom XNAT resource type into scans.
        headers (dict[str, str]): XNAT authentication headers.

    Returns:
        str: The path to the unzipped folder containing the downloaded images.

    Raises:
        imaging_api.utils.exceptions.NotFoundError: If the project with the given ID is not found, if no experiments are
        found for the given accession ID, if no data is found at the download URL, or if the ZIP file is not found after
        download.
        ValueError: If the net ID attempts path traversal outside the base images directory.
        Exception: If there is an error during any of the requests to XNAT, during the download process, or during the
        unzipping process.
    """
    # Validate net_id doesn't escape base images directory
    download_dir = os.path.join(BASE_IMAGES_DOWNLOAD_DIR, net_id)
    base_images_download_dir_abs = os.path.realpath(BASE_IMAGES_DOWNLOAD_DIR)
    download_dir_abs = os.path.realpath(download_dir)
    if not download_dir_abs.startswith(base_images_download_dir_abs + os.sep):
        raise ValueError(f"Path traversal detected in net ID: {net_id}")

    # Ensure the per-net download directory exists before we try to write into it.
    # A missing net_id subdir is a deployment/misconfiguration issue (the bind
    # mount was never provisioned for this net), not a "remote resource not
    # found" — surface it as LocalStorageError so the router returns 500.
    try:
        os.makedirs(download_dir_abs, exist_ok=True)
    except OSError as e:
        raise LocalStorageError(
            f"Cannot create image download directory {download_dir_abs!r} on the trust host: {e}"
        ) from e

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

    # Get subject ID from experiment
    subject_id = get_subject_id_from_experiment_response(experiment_response)

    # Format download URL
    download_url = format_download_url(
        project_id=project_id,
        subject_id=subject_id,
        experiment_id_or_label=accession_id,
        assessor_type=assessor_type,
        resource_type=resource_type,
    )
    logger.info(f"Download URL: {download_url}")

    # Define download and extraction paths. net_id was validated above; we
    # still have to guard the final zip path because accession_id and
    # resource_type are user-controlled and could smuggle `..` segments that
    # would escape download_dir_abs into a sibling net's directory or out of
    # BASE_IMAGES_DOWNLOAD_DIR entirely.
    zip_file_path = os.path.join(download_dir_abs, f"{accession_id}-scans-{resource_type}.zip")
    zip_file_path_abs = os.path.realpath(zip_file_path)
    if not zip_file_path_abs.startswith(download_dir_abs + os.sep):
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

    # Unzip file and rename the folder
    extracted_folder = unzip_file(downloaded_file, download_dir_abs, accession_id)
    logger.debug(f"Extracted folder: {extracted_folder}")

    # List contents of the extracted folder recursively
    for root, dirs, files in os.walk(extracted_folder):
        for name in files:
            logger.debug(f"File: {os.path.join(root, name)}")
        for name in dirs:
            logger.debug(f"Directory: {os.path.join(root, name)}")

    return extracted_folder


def format_download_url(
    project_id: str,
    subject_id: str,
    experiment_id_or_label: str,
    assessor_type: str = "scan",
    resource_type: str = "NIFTI",
):
    """
    Formats the XNAT API URL to download experiment scan images.

    Args:
        project_id (str): XNAT project ID.
        subject_id (str): XNAT subject ID.
        experiment_id_or_label (str): XNAT experiment ID or its label.
        assessor_type (str): Type of assessor (scan or assessor).
        resource_type (str): Resource type (e.g. `NIFTI`, `DICOM`, etc.).

    Returns:
        str: Formatted URL for downloading images.
    """
    assert assessor_type.lower() in [
        "scan",
        "assessor",
    ], "Type must be 'scan' or 'assessor'"
    return (
        f"{XNAT_URL}/data/projects/{project_id}/subjects/{subject_id}/"
        f"experiments/{experiment_id_or_label}/{assessor_type.lower()}s/"
        f"ALL/resources/{resource_type}/files?format=zip"
    )


def download_file(url: str, destination_path: str, headers: dict[str, str]):
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
        raise LocalStorageError(
            f"Cannot create parent directory {parent_dir!r} for downloaded file: {e}"
        ) from e

    try:
        with open(destination_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024):
                file.write(chunk)
    except OSError as e:
        # Permission denied, disk full, bind mount stale, etc. These are all
        # trust-host problems — not "the remote URL is missing".
        raise LocalStorageError(
            f"Failed to write downloaded file to {destination_path!r}: {e}"
        ) from e

    return destination_path


def unzip_file(zip_path: str, extract_dir: str, new_name: str):
    """
    Extracts a ZIP file and renames the directory.

    Args:
        zip_path (str): Path to the ZIP file.
        extract_dir (str): Directory to extract the contents.
        new_name (str): New name for the extracted directory.

    Returns:
        str: Path to the renamed directory.

    Raises:
        FileNotFoundError: If the ZIP file does not exist.
        ValueError: If the ZIP file contains path traversal entries (zip slip).
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    extract_dir_abs = os.path.realpath(extract_dir)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        members = zip_ref.namelist()

        for member in members:
            member_path = os.path.realpath(os.path.join(extract_dir_abs, member))
            if not member_path.startswith(extract_dir_abs + os.sep):
                raise ValueError(f"Attempted path traversal in ZIP entry: {member}")

        zip_ref.extractall(extract_dir_abs)

    # Rename the extracted directory
    extracted_dir = os.path.join(extract_dir_abs, Path(zip_path).stem)
    renamed_dir = os.path.join(extract_dir_abs, new_name)

    if os.path.exists(extracted_dir):
        os.rename(extracted_dir, renamed_dir)

    # Delete the original ZIP file
    os.remove(zip_path)

    return renamed_dir
