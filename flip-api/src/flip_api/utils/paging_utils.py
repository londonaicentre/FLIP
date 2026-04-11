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

import math
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flip_api.utils.logger import logger

# Define TypeVars for Generic Models
T = TypeVar("T")


class PagingInfo(BaseModel):
    offset: int
    page_number: int = Field(alias="pageNumberInt")
    page_size: int = Field(alias="pageSizeInt")
    search_str: str = Field(alias="searchStr")

    model_config = ConfigDict(
        populate_by_name=True,  # Allows using alias in constructor and for export
        validate_by_name=True,  # Allows using field name in constructor
    )


class FilterInfo(BaseModel):
    owner: UUID | None = Field(default=None)  # Assuming owner is a UUID

    model_config = ConfigDict(
        populate_by_name=True,  # Allows using alias in constructor and for export
        validate_by_name=True,  # Allows using field name in constructor
    )


class IPagedResponse(BaseModel, Generic[T]):
    data: list[T]
    total_rows: int


class IPagedData(BaseModel, Generic[T]):
    page: int
    page_size: int = Field(alias="pageSize")
    total_pages: int = Field(alias="totalPages")
    total_records: int = Field(alias="totalRecords")
    data: list[T]

    model_config = ConfigDict(
        populate_by_name=True,
    )


def get_paging_details(query_string_parameters: dict[str, str | int | float] | None = None) -> PagingInfo:
    """
    Parses query string parameters for pagination details.

    Args:
        query_string_parameters: A dictionary of query parameters.

    Returns:
        PagingInfo: An object containing offset, page number, page size, and search string.
    """
    if query_string_parameters is None:
        query_string_parameters = {}

    page_number_str = str(query_string_parameters.get("pageNumber", "1"))
    page_size_str = str(query_string_parameters.get("pageSize", "20"))
    search_val = query_string_parameters.get("search", "")
    search_str = str(search_val) if search_val is not None else ""

    page_number_int: int
    page_size_int: int

    try:
        page_number_int = int(page_number_str)
        if page_number_int <= 0:
            logger.warning(f"Invalid pageNumber '{page_number_str}', defaulting to 1.")
            page_number_int = 1
    except ValueError:
        logger.error(f"Could not parse pageNumber '{page_number_str}', setting default value 1.")
        page_number_int = 1

    try:
        page_size_int = int(page_size_str)
        if page_size_int <= 0:
            logger.warning(f"Invalid pageSize '{page_size_str}', defaulting to 20.")
            page_size_int = 20
    except ValueError:
        logger.error(f"Could not parse pageSize '{page_size_str}', setting default value 20.")
        page_size_int = 20

    offset = (page_number_int - 1) * page_size_int

    return PagingInfo(
        offset=offset,
        pageNumberInt=page_number_int,
        pageSizeInt=page_size_int,
        searchStr=search_str,
    )


def get_filter_details(query_string_parameters: dict[str, str | UUID] | None = None) -> FilterInfo:
    """
    Parses query string parameters for filter details.

    Args:
        query_string_parameters: A dictionary of query parameters.

    Returns:
        FilterInfo: An object containing filter criteria (e.g., owner_id).
    """
    if query_string_parameters is None:
        query_string_parameters = {}

    owner_param = query_string_parameters.get("owner")
    if owner_param is None:
        logger.debug("No owner parameter provided, defaulting to None.")
        owner_param = None
    else:
        try:
            # Attempt to convert the owner parameter to a UUID
            owner_param = UUID(str(owner_param))
        except ValueError:
            logger.warning(f"Invalid UUID format for owner parameter: {owner_param}. Treating as None.")
            owner_param = None

    # The Pydantic model FilterInfo will handle the validation of owner_param to UUID
    return FilterInfo(owner=owner_param)


def get_total_pages(total_records: int, page_size_int: int) -> int:
    """
    Calculates the total number of pages.

    Args:
        total_records: The total number of records.
        page_size_int: The number of records per page.

    Returns:
        int: The total number of pages.
    """
    if page_size_int <= 0:
        logger.warning("page_size_int is non-positive, returning 0 total pages.")
        return 0  # Or raise an error, or return 1 if total_records > 0
    if total_records <= 0:
        return 0  # No records, no pages (or 1 if you prefer to show an empty page)

    return math.ceil(total_records / page_size_int)
