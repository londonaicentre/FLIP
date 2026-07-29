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

"""Pagination must bound caller-supplied page sizes."""

from flip_api.config import get_settings
from flip_api.utils.paging_utils import get_paging_details

MAX = get_settings().MAX_PAGE_SIZE


def test_oversized_page_size_is_clamped():
    """Without a cap, one request can pull an unbounded number of rows into memory."""
    assert get_paging_details({"pageSize": "100000000"}).page_size == MAX


def test_page_size_at_the_cap_is_allowed():
    assert get_paging_details({"pageSize": str(MAX)}).page_size == MAX


def test_reasonable_page_size_is_untouched():
    assert get_paging_details({"pageSize": "25"}).page_size == 25


def test_zero_and_negative_fall_back_to_default():
    assert get_paging_details({"pageSize": "0"}).page_size == 20
    assert get_paging_details({"pageSize": "-5"}).page_size == 20


def test_non_numeric_falls_back_to_default():
    assert get_paging_details({"pageSize": "all"}).page_size == 20


def test_offset_uses_the_clamped_size():
    """The clamp must feed the offset too, or page 2 skips a phantom window."""
    paging = get_paging_details({"pageNumber": "2", "pageSize": "100000000"})
    assert paging.offset == MAX
