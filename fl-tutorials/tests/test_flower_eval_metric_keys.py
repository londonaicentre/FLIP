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
# Drift guard (FLIP#928): Flower client apps must not pin metrics to a fixed ".x_<digits>" coordinate.
#
# With best-model selection the evaluate phase runs every round, so a key like "test_loss.x_0" (or ".x_1")
# stacks one point per round at that x. A bare key already plots at the server round it was measured in.
# Intra-round progress computes its coordinate (f"train_loss@epoch.x_{cumulative_epoch}"), which is not matched.

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_APPS = sorted(
    [*REPO_ROOT.glob("fl-tutorials/flower/**/client_app.py"), *REPO_ROOT.glob("fl-apps/flower/**/client_app.py")]
)
FIXED_X_KEY = re.compile(r"\.x_\d+\b")


def test_client_apps_found() -> None:
    assert CLIENT_APPS


@pytest.mark.parametrize("key", ['"test_loss.x_0"', '"test_loss.x_1"', 'f"{k}.x_12"'])
def test_fixed_coordinates_are_caught(key: str) -> None:
    assert FIXED_X_KEY.search(key)


def test_computed_epoch_coordinate_is_not_caught() -> None:
    assert not FIXED_X_KEY.search('f"train_loss@epoch.x_{cumulative_epoch}"')


@pytest.mark.parametrize("path", CLIENT_APPS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_fixed_x_metric_keys(path: Path) -> None:
    match = FIXED_X_KEY.search(path.read_text())
    assert not match, f"{path}: drop the fixed '{match and match[0]}' coordinate, the bare key plots per round"
