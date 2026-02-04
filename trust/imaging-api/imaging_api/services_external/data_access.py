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
import pandas as pd
from pydantic import BaseModel, Field

from imaging_api.config import get_settings
from imaging_api.utils.logger import logger

DATA_ACCESS_API_URL = get_settings().DATA_ACCESS_API_URL
logger.info(f"Data Access API URL: {DATA_ACCESS_API_URL}")


class DataframeRequest(BaseModel):
    """Request model for the Data Access API to fetch a DataFrame."""

    encrypted_project_id: str = Field(..., description="The unique identifier for the project")
    query: str = Field(..., description="The raw SQL query to execute")


async def get_dataframe(encrypted_project_id: str, query: str) -> pd.DataFrame:
    """
    Calls the remote data access API endpoint using HTTPX and returns its raw JSON response.

    Args:
        encrypted_project_id (str): The encrypted project ID.
        query (str): The SQL query to execute.

    Returns:
        pd.DataFrame: The DataFrame containing the results of the query.
    """
    dataframe_request = DataframeRequest(encrypted_project_id=encrypted_project_id, query=query)

    logger.debug(f"get_dataframe: Sending request to Data Access API with {encrypted_project_id=} and {query=}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{DATA_ACCESS_API_URL}/cohort/dataframe",
                json=dataframe_request.model_dump(),
            )

        response.raise_for_status()
        dataframe = pd.DataFrame(response.json())
        return dataframe

    except httpx.HTTPError as exc:
        error_message = f"get_dataframe: HTTP error occurred while calling the Data Access API: {exc}"
        logger.error(error_message)
        raise RuntimeError(error_message) from exc
