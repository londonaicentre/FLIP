# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the e2e smoke's --trusts subset selection (select_trusts)."""

import pytest

from tests.e2e_smoke import SmokeFailure, select_trusts

TRUSTS = [
    {"id": "id-1", "name": "Guy's and St Thomas' Trust", "code": "GSTT"},
    {"id": "id-2", "name": "Bangkok Dusit Medical Services", "code": "BDMS"},
]


def test_no_selection_returns_all_trusts() -> None:
    assert select_trusts(TRUSTS, None) == TRUSTS
    assert select_trusts(TRUSTS, "") == TRUSTS


def test_selects_by_code_case_insensitive() -> None:
    assert select_trusts(TRUSTS, "gstt") == [TRUSTS[0]]
    assert select_trusts(TRUSTS, "GSTT") == [TRUSTS[0]]


def test_selects_by_name_case_insensitive() -> None:
    assert select_trusts(TRUSTS, "bangkok dusit medical services") == [TRUSTS[1]]


def test_selects_multiple_comma_separated_tokens() -> None:
    assert select_trusts(TRUSTS, "BDMS, gstt") == TRUSTS


def test_unknown_token_raises_with_registered_roster() -> None:
    with pytest.raises(SmokeFailure, match="not registered on the hub.*kch.*GSTT"):
        select_trusts(TRUSTS, "KCH")


def test_trust_without_code_matches_by_name() -> None:
    trusts = [{"id": "id-3", "name": "No Code Trust", "code": None}]
    assert select_trusts(trusts, "no code trust") == trusts
