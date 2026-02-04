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

from flip_api.utils.formatters import to_pascal_case


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("can_access_admin_panel", "CanAccessAdminPanel"),
        ("CAN_ACCESS_ADMIN_PANEL", "CanAccessAdminPanel"),
        ("can_access", "CanAccess"),
        ("user", "User"),
        ("", ""),
        ("_", ""),
        ("__init__", "Init"),
        ("_private_var", "PrivateVar"),
        ("alreadyPascal", "Alreadypascal"),
    ],
)
def test_to_pascal_case(input_str, expected):
    assert to_pascal_case(input_str) == expected
