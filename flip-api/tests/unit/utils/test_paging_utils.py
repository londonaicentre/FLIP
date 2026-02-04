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

from flip_api.utils.paging_utils import get_total_pages

# Absolute import of function to test


@pytest.mark.parametrize(
    ("total_records", "page_size_int", "expected_pages"),
    [
        (0, 10, 0),  # No records, no pages
        (100, 0, 0),  # Page size is zero
        (100, -5, 0),  # Page size is negative
        (-10, 5, 0),  # Total records is negative
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
def test_get_total_pages_various_scenarios(total_records, page_size_int, expected_pages):
    assert get_total_pages(total_records, page_size_int) == expected_pages


def test_get_total_pages_page_size_zero_logs_warning(caplog):
    get_total_pages(100, 0)
    assert "page_size_int is non-positive, returning 0 total pages." in caplog.text


def test_get_total_pages_page_size_negative_logs_warning(caplog):
    get_total_pages(100, -10)
    assert "page_size_int is non-positive, returning 0 total pages." in caplog.text


def test_get_total_pages_total_records_zero():
    assert get_total_pages(0, 10) == 0


def test_get_total_pages_total_records_negative():
    assert get_total_pages(-5, 10) == 0
