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

from data_access_api.utils.sql_parsers import extract_missing_identifier


@pytest.mark.parametrize(
    ("error_msg", "pattern", "expected"),
    [
        (
            'relation "omop.some_missing_table" does not exist',
            r'relation "([^"]+)" does not exist',
            "omop.some_missing_table",
        ),
        (
            'column "birth_date" does not exist',
            r'column "([^"]+)" does not exist',
            "birth_date",
        ),
        (
            "some unrelated error",
            r'column "([^"]+)" does not exist',
            "unknown",
        ),
        (
            "",  # empty string input
            r'column "([^"]+)" does not exist',
            "unknown",
        ),
    ],
)
def test_extract_missing_identifier(error_msg, pattern, expected):
    assert extract_missing_identifier(error_msg, pattern) == expected
