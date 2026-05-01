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

# Interacts with Data Access API

import httpx
from pydantic import BaseModel, Field

from imaging_api.config import get_settings
from imaging_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
logger.info(f"Data Access API URL: {DATA_ACCESS_API_URL}")


class AccessionIdsRequest(BaseModel):
    """Request model for the Data Access API to fetch a cohort's accession IDs."""

    encrypted_project_id: str = Field(..., description="The unique identifier for the project")
    query: str = Field(..., description="The raw SQL query to execute")


async def get_accession_ids(encrypted_project_id: str, query: str) -> list[str]:
    """
    Calls the data-access-api ``/cohort/accession-ids`` endpoint and returns the
    list of accession IDs for the cohort.

    The endpoint projects the cohort query to the ``accession_id`` column
    server-side, so no other patient attributes leave the trust's data store.

    Args:
        encrypted_project_id (str): The encrypted project ID.
        query (str): The SQL query to execute.

    Returns:
        list[str]: The accession IDs returned by the cohort query, in query order.

    Raises:
        RuntimeError: If the HTTP call to the Data Access API fails (network error or non-2xx
            response).
    """
    request = AccessionIdsRequest(encrypted_project_id=encrypted_project_id, query=query)

    logger.debug(f"get_accession_ids: Sending request to Data Access API with {encrypted_project_id=}")

    headers = {
        get_settings().TRUST_INTERNAL_SERVICE_KEY_HEADER: get_settings().TRUST_INTERNAL_SERVICE_KEY,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{DATA_ACCESS_API_URL}/cohort/accession-ids",
                json=request.model_dump(),
                headers=headers,
            )

        response.raise_for_status()
        return list(response.json().get("accession_ids", []))

    except httpx.HTTPError as exc:
        error_message = f"get_accession_ids: HTTP error occurred while calling the Data Access API: {exc}"
        logger.error(error_message)
        raise RuntimeError(error_message) from exc
