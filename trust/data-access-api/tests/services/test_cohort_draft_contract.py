# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
"""Contract test for the hub's cohort SQL drafting (FLIP#1556).

The hub teaches its drafting model with example queries and serves canned drafts in development. Each
of them must pass this trust's authoritative ``validate_query``, function allowlist included, so a
pattern every trust would refuse is caught here rather than after a fan-out. The hub never imports
this validator: this is a test-time check, not a copy of the policy.
"""

from pathlib import Path

import pytest

from data_access_api.services.cohort import validate_query

ASSETS = Path(__file__).resolve().parents[4] / "flip-api/src/flip_api/cohort_services/drafting/assets"
DRAFTS = sorted([*ASSETS.glob("examples/*.sql"), *ASSETS.glob("fake/*.sql")])


def test_the_assets_are_found():
    assert {p.parent.name for p in DRAFTS} == {"examples", "fake"}


@pytest.mark.parametrize("path", DRAFTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_hub_draft_passes_the_trust_validator(path: Path):
    validate_query(path.read_text())
