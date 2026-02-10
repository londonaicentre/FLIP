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

from imaging_api.routers.schemas import UploadDataRequest
from imaging_api.services.upload import upload_data_to_xnat
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.encryption import decrypt
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.logger import logger

# Create router
router = APIRouter(prefix="/upload", tags=["Upload"])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.put("/images/{net_id}", summary="Upload data to XNAT")
async def upload_data(net_id: str, request_data: UploadDataRequest, headers: XNATAuthHeaders) -> list[str]:
    """
    Upload data to XNAT for a given accession ID and central hub project ID

    Args:
        net_id (str): The NVFlare net ID.
        request_data (UploadDataRequest): The request data containing the encrypted central hub project ID and the
        accession ID.
        headers (XNATAuthHeaders): The XNAT authentication headers.

    Returns:
        list[str]: List of URLs of the uploaded files.

    Raises:
        HTTPException: If there is an error during the upload process or if the request cannot be processed.
    """
    # Decrypt project ID
    logger.info("Trying to decrypt Central Hub Project ID")
    try:
        central_hub_project_id = decrypt(request_data.encrypted_central_hub_project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to decrypt IDs: {str(e)}")

    try:
        uploaded_files = await upload_data_to_xnat(
            central_hub_project_id=central_hub_project_id,
            accession_id=request_data.accession_id,
            net_id=net_id,
            scan_id=request_data.scan_id,
            resource_id=request_data.resource_id,
            files_relative_paths_to_upload=request_data.files,
            exist_ok=request_data.exist_ok,
            headers=headers,
        )
        return uploaded_files
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Resource not found: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload files: {str(e)}")
