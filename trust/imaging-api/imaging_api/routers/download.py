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

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from imaging_api.routers.schemas import (
    DownloadImagesRequestData,
    DownloadImagesResponse,
    ResourceType,
    StudyLayoutResponse,
)
from imaging_api.services.archive_layout import study_layout
from imaging_api.services.download import download_and_unzip_images, tar_study
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import PROJECT_ID_CONTEXT, decrypt
from imaging_api.utils.exceptions import LocalStorageError, NotFoundError
from imaging_api.utils.internal_auth import authenticate_internal_service
from imaging_api.utils.logger import logger

router = APIRouter(prefix="/download", tags=["Download"], dependencies=[Depends(authenticate_internal_service)])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


def open_project_id(encrypted_central_hub_project_id: str) -> str:
    """Decrypt the hub's sealed project id, or raise the HTTP error that says why it could not be opened."""
    logger.info("Trying to decrypt Central Hub Project ID")
    try:
        return decrypt(encrypted_central_hub_project_id, context=PROJECT_ID_CONTEXT)
    except InvalidTag:
        # The caller's own payload is bad (tampered, sealed for another purpose, or the hub's
        # AES_KEY_BASE64 is not this trust's): a 400 that says so, not a 500 with an empty reason.
        logger.error("Central Hub project id failed authentication")
        raise HTTPException(status_code=400, detail="Central Hub project id failed authentication")
    except (ValueError, KeyError) as e:
        logger.error(f"Central Hub project id is not a valid envelope: {e}")
        raise HTTPException(status_code=400, detail=f"Central Hub project id is not a valid envelope: {e}")
    except Exception as e:
        # Not the caller's payload: a key that cannot be loaded, or a fault in the cipher itself.
        # Name the type so an empty exception message never yields a blank reason.
        logger.exception(f"Failed to decrypt Central Hub project id ({type(e).__name__}): {e}")
        raise HTTPException(status_code=500, detail=f"Failed to decrypt Central Hub project id ({type(e).__name__})")


async def _cached_study(
    net_id: str,
    request_data: DownloadImagesRequestData,
    assessor_type: str,
    resource_type: ResourceType,
    force_refresh: bool,
    headers: dict[str, str],
) -> str:
    """Fill the cache for one (accession, resource) and return its folder, mapping service errors to HTTP ones."""
    central_hub_project_id = open_project_id(request_data.encrypted_central_hub_project_id)
    try:
        return await download_and_unzip_images(
            central_hub_project_id=central_hub_project_id,
            accession_id=request_data.accession_id,
            net_id=net_id,
            assessor_type=assessor_type,
            resource_type=resource_type,
            headers=headers,
            force_refresh=force_refresh,
        )
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


@router.post("/images/{net_id}", summary="Download XNAT Data", response_model=DownloadImagesResponse)
async def download_images_by_accession_number(
    net_id: str,
    request_data: DownloadImagesRequestData,
    *,
    # TODO Make assessor_type an Enum with allowed values ("scan", "assessor")
    assessor_type: str = "scan",
    resource_type: ResourceType = "NIFTI",
    force_refresh: bool = False,
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
        resource_type (ResourceType): XNAT resource type, one of DICOM, NIFTI, SEG or ALL. ALL will download all
            resources.
        force_refresh (bool): Re-download even when a completed local copy is cached; the cache
        marker is rewritten after the re-download succeeds. Default False.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        DownloadImagesResponse: The path where the downloaded and unzipped images are stored.

    Raises:
        HTTPException: If there is an error during decryption, if the resource is not found, or if there is an error
                       during download/unzipping.
    """
    folder = await _cached_study(net_id, request_data, assessor_type, resource_type, force_refresh, headers)
    return DownloadImagesResponse(path=folder)


@router.post("/images/{net_id}/stream", summary="Download XNAT Data as a tar stream")
async def stream_images_by_accession_number(
    net_id: str,
    request_data: DownloadImagesRequestData,
    *,
    assessor_type: str = "scan",
    resource_type: ResourceType = "NIFTI",
    force_refresh: bool = False,
    headers: XNATAuthHeaders,
) -> StreamingResponse:
    """
    The route above for a client with no mount of the net's images folder (FLIP#1049): the same sealed project id
    and the same cache fill, then the cached study as an uncompressed tar rooted at ``<accession_id>/``.

    Args:
        net_id (str): The ID of the FL net that will run the training.
        request_data (DownloadImagesRequestData): Contains encrypted central hub project ID and accession ID.
        assessor_type (str): The type of assessor to use for the download. Default is "scan". Can be "assessor".
        resource_type (ResourceType): XNAT resource type, one of DICOM, NIFTI, SEG or ALL.
        force_refresh (bool): Re-download even when a completed local copy is cached. Default False.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        StreamingResponse: ``application/x-tar``, the study's files; the cache's own sentinels are left out.
    """
    folder = await _cached_study(net_id, request_data, assessor_type, resource_type, force_refresh, headers)
    return StreamingResponse(tar_study(folder), media_type="application/x-tar")


@router.post(
    "/images/{net_id}/layout",
    summary="List where a study's files sit in XNAT's archive",
    response_model=StudyLayoutResponse,
)
def study_layout_by_accession_number(
    net_id: str,
    request_data: DownloadImagesRequestData,
    *,
    resource_type: ResourceType = "NIFTI",
    headers: XNATAuthHeaders,
) -> StudyLayoutResponse:
    """
    For a client that mounts its project's directory of XNAT's archive (FLIP#1857): names each scan file's place in
    that directory and in the layout a download has, so the client can read the study in place. No file content,
    and nothing is cached. Assessor resources are not listed; such a client streams them.

    Args:
        net_id (str): The ID of the FL net that will run the training.
        request_data (DownloadImagesRequestData): Contains encrypted central hub project ID and accession ID.
        resource_type (ResourceType): XNAT resource type, one of DICOM, NIFTI, SEG or ALL.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        StudyLayoutResponse: The study's files.

    Raises:
        HTTPException: 404 if the project, the study or its files are not in XNAT, 500 for any other failure.
    """
    central_hub_project_id = open_project_id(request_data.encrypted_central_hub_project_id)
    try:
        return StudyLayoutResponse.model_validate(
            {"files": study_layout(central_hub_project_id, request_data.accession_id, resource_type, headers)}
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Resource not found: {e}")
    except Exception as e:
        logger.error(f"Failed to list the study's files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list the study's files: {e}")
