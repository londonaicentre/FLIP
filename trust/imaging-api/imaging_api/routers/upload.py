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

import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import ValidationError

from imaging_api.routers.download import open_project_id
from imaging_api.routers.schemas import UploadDataRequest
from imaging_api.services.upload import stage_uploads, upload_data_to_xnat
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.internal_auth import authenticate_internal_service

# Create router
router = APIRouter(prefix="/upload", tags=["Upload"], dependencies=[Depends(authenticate_internal_service)])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.put("/images/{net_id}", summary="Upload data to XNAT")
async def upload_data(net_id: str, request_data: UploadDataRequest, headers: XNATAuthHeaders) -> list[str]:
    """
    Upload data to XNAT for a given accession ID and central hub project ID

    Args:
        net_id (str): The FL net ID.
        request_data (UploadDataRequest): The request data containing the encrypted central hub project ID and the
        accession ID.
        headers (XNATAuthHeaders): The XNAT authentication headers.

    Returns:
        list[str]: List of URLs of the uploaded files.

    Raises:
        HTTPException: If there is an error during the upload process or if the request cannot be processed.
    """
    central_hub_project_id = open_project_id(request_data.encrypted_central_hub_project_id)

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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid upload request: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload files: {str(e)}")


@router.put("/images/{net_id}/files", summary="Upload files to XNAT")
async def upload_files(
    net_id: str,
    headers: XNATAuthHeaders,
    files: list[UploadFile],
    encrypted_central_hub_project_id: Annotated[str, Form()],
    accession_id: Annotated[str, Form()],
    scan_id: Annotated[str, Form()],
    resource_id: Annotated[str, Form()],
    exist_ok: Annotated[bool, Form()] = False,
) -> list[str]:
    """
    The route above for a client with no mount of the net's upload folder (FLIP#1049): the files travel in the
    request, are staged under ``<net>/upload/<request id>/`` for the upload, and are removed afterwards.

    Args:
        net_id (str): The FL net ID.
        headers (XNATAuthHeaders): The XNAT authentication headers.
        files (list[UploadFile]): The files to upload, each by its own name.
        encrypted_central_hub_project_id (str): The hub's sealed project id.
        accession_id (str): The XNAT experiment label.
        scan_id (str): The scan to upload into, created if missing.
        resource_id (str): The resource to upload into, created if missing.
        exist_ok (bool): Whether to replace a file of the same name already in the resource.

    Returns:
        list[str]: List of URLs of the uploaded files.
    """
    try:
        staging_dir, relative_paths = await stage_uploads(net_id, files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid upload request: {str(e)}")
    try:
        request_data = UploadDataRequest(
            encrypted_central_hub_project_id=encrypted_central_hub_project_id,
            accession_id=accession_id,
            scan_id=scan_id,
            resource_id=resource_id,
            files=relative_paths,
            exist_ok=exist_ok,
        )
    except ValidationError as e:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
    try:
        return await upload_data(net_id, request_data, headers)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
