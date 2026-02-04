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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from flip_api.db.database import get_session
from flip.db.models.main_models import Trust
from flip.domain.interfaces.trust import ITrustHealth
from flip.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# [#114] ✅
@router.get("/health", status_code=status.HTTP_200_OK)
async def check_trusts_health(
    request: Request,
    db: Session = Depends(get_session),
):
    """
    Retrieves health status of all trusts by checking their health endpoints
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

        response = []
        logger.debug("Sending a request to each of the trusts...")

        # Make concurrent requests to all trusts
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = []

            for row in result:
                tasks.append(check_trust_health(client=client, trust=row, headers=dict(request.headers)))

            # Wait for all requests to complete
            trust_health_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out any exception results
            response = [res.model_dump() for res in trust_health_results if isinstance(res, ITrustHealth)]

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
        client: AsyncClient for HTTP requests
        trust: Trust model instance
        auth_header: Authorization header value

    Returns:
        ITrustHealth: Trust health status
    """
    try:
        # Send request to trust health endpoint
        response = await client.get(f"{trust.endpoint}/health", headers=headers)

        # If the request was successful (status code 200)
        if response.status_code == 200:
            return ITrustHealth(trust_id=str(trust.id), trust_name=trust.name, online=True)
        else:
            # If the response was not 200, treat it as unhealthy
            return ITrustHealth(trust_id=str(trust.id), trust_name=trust.name, online=False)

    except Exception as e:
        logger.error(f"{trust.name} failed to report back. Error message: {str(e)}")

        # If an exception is raised during the request, mark the trust as offline
        return ITrustHealth(trust_id=str(trust.id), trust_name=trust.name, online=False)
