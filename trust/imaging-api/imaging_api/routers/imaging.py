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

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException

from imaging_api.routers.schemas import ImportStudyRequest, ImportStudyResponse, PacsStatus, Study
from imaging_api.services.imaging import (
    ping_pacs,
    query_by_accession_number,
    queue_image_import_request,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError

router = APIRouter(prefix="/imaging", tags=["Imaging"])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.get("/ping_pacs/{pacs_id}", summary="Ping Imaging Provider (PACS) by ID")
def ping_pacs_endpoint(pacs_id: int, headers: XNATAuthHeaders) -> PacsStatus:
    """
    Pings the imaging provider (PACS) to check if it is reachable.

    Args:
        pacs_id (int): PACS ID to ping
    Returns:
        PacsStatus: Status of the PACS system
    """
    try:
        return ping_pacs(pacs_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/query_by_accession_number",
    summary="Query Imaging Provider (PACS) by Accession Number",
)
def query_by_accession_number_endpoint(accession_number: str, headers: XNATAuthHeaders) -> List[Study]:
    """
    Queries the imaging provider (PACS) to retrieve a list of studies associated with the provided accession number.

    Args:
        accession_number (str): Accession number for the study
    Returns:
        List[Study]: List of studies associated with the provided accession number
    """
    try:
        return query_by_accession_number(accession_number, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/queue_image_import_request",
    summary="Queue Image Import Request from Imaging Provider (PACS)",
)
def queue_image_import_request_endpoint(
    import_request: ImportStudyRequest, headers: XNATAuthHeaders
) -> List[ImportStudyResponse]:
    """
    Queues a job within the imaging provider (PACS) to import the images from the image store.

    Args:
        import_request (ImportStudyRequest): Import request containing the project ID and list of studies
    Returns:
        List[ImportStudyResponse]: List of import responses for the queued studies
    """
    try:
        return queue_image_import_request(import_request, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
