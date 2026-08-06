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

"""Unit tests for the demo project seeder's --states subset selection (select_entries)."""

import pytest

from tests.e2e_smoke import SmokeFailure
from tests.seed_demo_projects import select_entries

CATALOGUE = [
    {"name": "Spleen", "state": "approved_model"},
    {"name": "Pneumothorax", "state": "staged"},
    {"name": "Cardiomegaly", "state": "unstaged"},
]


def test_no_selection_returns_full_catalogue() -> None:
    assert select_entries(CATALOGUE, None) == CATALOGUE
    assert select_entries(CATALOGUE, "") == CATALOGUE


def test_selects_requested_states_only() -> None:
    assert select_entries(CATALOGUE, "staged,unstaged") == CATALOGUE[1:]


def test_whitespace_and_empty_tokens_are_tolerated() -> None:
    assert select_entries(CATALOGUE, " staged , ,unstaged ") == CATALOGUE[1:]


def test_unknown_state_raises_with_known_ladder() -> None:
    with pytest.raises(SmokeFailure, match="Unknown --states value.*bogus.*unstaged"):
        select_entries(CATALOGUE, "staged,bogus")
