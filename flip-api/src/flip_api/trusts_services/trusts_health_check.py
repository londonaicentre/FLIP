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

import asyncio

import httpx
from flip_api.utils.http import _trust_ssl_context
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import ITrustHealth
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# [#114] ✅
@router.get("/health", status_code=status.HTTP_200_OK, response_model=list[ITrustHealth])
async def check_trusts_health(
    request: Request,
    db: Session = Depends(get_session),
) -> list[ITrustHealth]:
    """
    Retrieves health status of all trusts by checking their health endpoints.

    Args:
        request (Request): The incoming HTTP request, used to pass headers to trust health endpoints.
        db (Session): Database session for querying trusts.

    Returns:
        list[ITrustHealth]: A list of ITrustHealth objects representing the health status of each trust.

    Raises:
        HTTPException: If no trusts are found in the database or if there is an error during the operation.
    """
    try:
        logger.debug("Attempting to retrieve trusts from the database...")

        # Using SQLModel select instead of raw SQL
        statement = select(Trust)
        result = db.exec(statement).all()

        logger.debug(f"Found {len(result)} trusts.")

        if not result:
            logger.warning("No trusts found in the database")
            raise HTTPException(status_code=404, detail="No trusts found")

        logger.debug("Sending a request to each of the trusts...")

        # Make concurrent requests to all trusts
        async with httpx.AsyncClient(timeout=10.0, verify=_trust_ssl_context()) as client:
            tasks = []

            for row in result:
                tasks.append(check_trust_health(client=client, trust=row, headers=dict(request.headers)))

            # Wait for all requests to complete
            trust_health_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out any exception results
            response: list[ITrustHealth] = [res for res in trust_health_results if isinstance(res, ITrustHealth)]

        logger.info(f"Successfully retrieved health status for {len(response)} trusts")
        logger.debug(f"Trusts health status: {response}")

        return response

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error checking trusts health: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


async def check_trust_health(client: httpx.AsyncClient, trust: Trust, headers: dict) -> ITrustHealth:
    """
    Check health of a single trust

    Args:
        client (httpx.AsyncClient): AsyncClient for HTTP requests
        trust (Trust): Trust model instance
        headers (dict): Headers to include in the request to the trust's health endpoint

    Returns:
        ITrustHealth: Trust health status
    """
    try:
        # Send request to trust health endpoint
        response = await client.get(f"{trust.endpoint}/health", headers=headers)

        # If the request was successful (status code 200)
        if response.status_code == 200:
            return ITrustHealth(trust_id=trust.id, trust_name=trust.name, online=True)  # type: ignore[call-arg]
        else:
            # If the response was not 200, treat it as unhealthy
            return ITrustHealth(trust_id=trust.id, trust_name=trust.name, online=False)  # type: ignore[call-arg]

    except Exception as e:
        logger.error(f"{trust.name} failed to report back. Error message: {str(e)}")

        # If an exception is raised during the request, mark the trust as offline
        return ITrustHealth(trust_id=trust.id, trust_name=trust.name, online=False)  # type: ignore[call-arg]
