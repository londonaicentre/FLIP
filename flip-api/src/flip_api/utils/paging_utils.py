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
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from flip_api.domain.schemas.status import ProjectType

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
    owner: UUID | None = Field(default=None)
    # FLIP#1071: restrict the list to imaging projects or tabular-only ones.
    # None = every type; the UI's "All types" segment sends no parameter.
    project_type: ProjectType | None = Field(default=None)

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


def paging_params(
    page_number: Annotated[int, Query(alias="pageNumber", ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1)] = 20,
    search: str = "",
) -> PagingInfo:
    """The ``pageNumber`` / ``pageSize`` / ``search`` query params of a paged list route, as a dependency."""
    return PagingInfo(
        offset=(page_number - 1) * page_size,
        pageNumberInt=page_number,
        pageSizeInt=page_size,
        searchStr=search,
    )


def get_total_pages(total_records: int, page_size: int) -> int:
    """The number of pages ``total_records`` fill; ``paging_params`` keeps ``page_size`` at 1 or more."""
    return math.ceil(total_records / page_size)
