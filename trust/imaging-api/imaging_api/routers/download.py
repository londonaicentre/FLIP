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

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from imaging_api.routers.schemas import DownloadImagesRequestData, DownloadImagesResponse
from imaging_api.services.download import download_and_unzip_images
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import decrypt
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError
from imaging_api.utils.internal_auth import authenticate_internal_service
from imaging_api.utils.logger import logger

router = APIRouter(prefix="/download", tags=["Download"], dependencies=[Depends(authenticate_internal_service)])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.post("/images/{net_id}", summary="Download XNAT Data", response_model=DownloadImagesResponse)
async def download_images_by_accession_number(
    net_id: str,
    request_data: DownloadImagesRequestData,
    *,
    # TODO Make assessor_type an Enum with allowed values ("scan", "assessor")
    assessor_type: str = "scan",
    resource_type: str = "NIFTI",
    headers: XNATAuthHeaders,
) -> DownloadImagesResponse:
    """
    Downloads XNAT experiment image data corresponding to a given accession ID and encrypted central hub project ID.

    If data exists for the experiment, all image scan resources will be downloaded and the location returned.

    All data is downloaded in a single .zip file. Once the file download is complete, this method will then attempt to
    unzip the folder and delete the existing .zip file.

    Args:
        net_id (str): The ID of the FL net that will run the training.
        request_data (DownloadImagesRequestData): Contains encrypted central hub project ID and accession ID.
        assessor_type (str): The type of assessor to use for the download. Default is "scan". Can be "assessor".
        resource_type (str): XNAT resource type e.g DICOM/NIFTI. ALL will download all resources. Custom value is
        allowed if researcher has added their own custom XNAT resource type into scans.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        DownloadImagesResponse: The path where the downloaded and unzipped images are stored.

    Raises:
        HTTPException: If there is an error during decryption, if the resource is not found, or if there is an error
                       during download/unzipping.
    """
    # Decrypt project ID
    logger.info("Trying to decrypt Central Hub Project ID")
    try:
        central_hub_project_id = decrypt(request_data.encrypted_central_hub_project_id)
    except Exception as e:
        error_msg = f"Failed to decrypt Central Hub Project ID: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    try:
        downloaded_folder = await download_and_unzip_images(
            central_hub_project_id=central_hub_project_id,
            accession_id=request_data.accession_id,
            net_id=net_id,
            assessor_type=assessor_type,
            resource_type=resource_type,
            headers=headers,
        )
        return DownloadImagesResponse(path=downloaded_folder)
    except NotFoundError as e:
        error_msg = f"Resource not found: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    except LocalStorageError as e:
        # Trust-host misconfiguration (bind mount missing, perms wrong, disk
        # full, etc.). This is NOT a 404 — the remote data may exist fine —
        # so do not return it as "resource not found", which was the bug this
        # branch addresses.
        error_msg = f"Trust-side storage error: {e.detail}"
        logger.error(error_msg)
        raise HTTPException(status_code=e.status_code, detail=error_msg)
    except ValueError as e:
        error_msg = f"Invalid download request: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        error_msg = f"Failed to download and unzip images: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
