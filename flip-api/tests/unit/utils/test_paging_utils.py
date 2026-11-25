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

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from flip_api.utils.paging_utils import PagingInfo, get_total_pages, paging_params


@pytest.mark.parametrize(
    ("total_records", "page_size", "expected_pages"),
    [
        (0, 10, 0),  # No records, no pages
        (10, 20, 1),  # Records less than page size
        (20, 20, 1),  # Records equal to page size
        (100, 20, 5),  # Records are a multiple of page size
        (101, 20, 6),  # Records are not a multiple of page size
        (99, 20, 5),  # Records are not a multiple of page size (boundary)
        (1, 1, 1),  # Single record, single page size
        (5, 1, 5),  # Multiple records, single page size
        (1000000, 100, 10000),  # Large numbers
    ],
)
def test_get_total_pages_various_scenarios(total_records, page_size, expected_pages):
    assert get_total_pages(total_records, page_size) == expected_pages


app = FastAPI()


@app.get("/paged")
def paged(paging: PagingInfo = Depends(paging_params)) -> PagingInfo:
    return paging


def test_paging_params_defaults_and_offset():
    client = TestClient(app)

    assert client.get("/paged").json() == {"offset": 0, "pageNumberInt": 1, "pageSizeInt": 20, "searchStr": ""}
    assert client.get("/paged", params={"pageNumber": 3, "pageSize": 5, "search": "ct"}).json() == {
        "offset": 10,
        "pageNumberInt": 3,
        "pageSizeInt": 5,
        "searchStr": "ct",
    }


@pytest.mark.parametrize("params", [{"pageSize": 0}, {"pageNumber": -1}, {"pageSize": "abc"}])
def test_paging_params_rejects_bad_values(params):
    assert TestClient(app).get("/paged", params=params).status_code == 422
