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

from fastapi import APIRouter, status

from trust_api.config import get_settings
from trust_api.routers.schemas import CohortQueryInput
from trust_api.utils.http import make_request
from trust_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
CENTRAL_HUB_API_URL = get_settings().CENTRAL_HUB_API_URL
PRIVATE_API_KEY = get_settings().PRIVATE_API_KEY
PRIVATE_API_KEY_HEADER = get_settings().PRIVATE_API_KEY_HEADER

# Create Router
router = APIRouter(prefix="/cohort", tags=["Cohort"])


@router.post("", status_code=status.HTTP_200_OK)
async def post_cohort_query(query_input: CohortQueryInput):
    """
    Calls the remote /cohort endpoint using the shared make_request utility and returns its raw JSON response.

    Args:
        query_input (trust_api.routers.schemas.CohortQueryInput): The input data for the cohort query.
    """
    logger.debug(f"Received cohort query: {query_input.model_dump()}")

    # Post cohort query to the data access API
    response = await make_request(
        method="POST",
        url=f"{DATA_ACCESS_API_URL}/cohort",
        json_body=query_input.model_dump(),
    )

    logger.debug(f"Response from /cohort endpoint: {response}")

    # Sends the statistics of the query to the CH
    logger.debug("Sending cohort query results to the central hub")
    logger.debug(f"url: {CENTRAL_HUB_API_URL}/cohort/results")

    # Convert all 'value' fields to strings before sending
    for group in response.get("data", []):  # type: ignore[attr-defined]
        for result in group.get("results", []):  # type: ignore[attr-defined]
            if "value" in result:
                result["value"] = str(result["value"])

    response = await make_request(
        method="POST",
        url=f"{CENTRAL_HUB_API_URL}/cohort/results",
        json_body=response,
        headers={PRIVATE_API_KEY_HEADER: PRIVATE_API_KEY},
    )
    logger.debug(f"Response from /cohort/results endpoint: {response}")
