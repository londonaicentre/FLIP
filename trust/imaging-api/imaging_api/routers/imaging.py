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

from imaging_api.routers.schemas import ImportStudyRequest, ImportStudyResponse, PacsStatus, Study
from imaging_api.services.imaging import (
    ping_pacs,
    query_by_accession_number,
    queue_image_import_request,
    resolve_pacs_id,
)
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError
from imaging_api.utils.internal_auth import authenticate_internal_service

router = APIRouter(prefix="/imaging", tags=["Imaging"], dependencies=[Depends(authenticate_internal_service)])

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]


@router.get("/ping_pacs", summary="Ping the registered Imaging Provider (PACS)")
@router.get("/ping_pacs/{pacs_id}", summary="Ping Imaging Provider (PACS) by ID")
def ping_pacs_endpoint(headers: XNATAuthHeaders, pacs_id: int | None = None) -> PacsStatus:
    """Pings the imaging provider (PACS) to check if it is reachable.

    Two routes, one handler. Without an id the PACS is resolved from XNAT the same way the import
    path resolves it — the id XNAT assigns at registration is not knowable in advance, so a caller
    that only wants to know whether the trust's PACS answers should not have to guess one. The
    by-id route stays for callers that genuinely mean a specific registration.

    Args:
        headers (XNATAuthHeaders): XNAT authentication headers.
        pacs_id (int | None): PACS ID to ping. Resolved from XNAT when omitted.

    Returns:
        PacsStatus: Status of the PACS system.

    Raises:
        HTTPException: If PACS is not found or if there is an error during the ping operation.
    """
    try:
        return ping_pacs(resolve_pacs_id(headers) if pacs_id is None else pacs_id, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/query_by_accession_number",
    summary="Query Imaging Provider (PACS) by Accession Number",
)
def query_by_accession_number_endpoint(accession_number: str, headers: XNATAuthHeaders) -> list[Study]:
    """
    Queries the imaging provider (PACS) to retrieve a list of studies associated with the provided accession number.

    Args:
        accession_number (str): Accession number for the study.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        list[Study]: List of studies associated with the provided accession number.

    Raises:
        HTTPException: If no studies are found for the given accession number or if there is an error during the query.
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
) -> list[ImportStudyResponse]:
    """
    Queues a job within the imaging provider (PACS) to import the images from the image store.

    Args:
        import_request (ImportStudyRequest): Import request containing the project ID and list of studies.
        headers (XNATAuthHeaders): XNAT authentication headers.

    Returns:
        list[ImportStudyResponse]: List of import responses for the queued studies.

    Raises:
        HTTPException: If there is an error during the queuing of the image import request or if the request cannot be
        processed.
    """
    try:
        return queue_image_import_request(import_request, headers)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
