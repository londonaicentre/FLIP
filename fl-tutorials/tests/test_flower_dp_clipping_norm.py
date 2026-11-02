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
"""A Flower tutorial trains with the same DP clipping norm on the platform as in the simulator (FLIP#1754).

The simulator reads the tutorial's pyproject.toml; the platform builds the job from FLIP's generic template
(fl-apps/flower/standard), whose dp-clipping-norm is a 1.0 placeholder, and applies only the uploaded
app/config.toml on top. A norm sized to the model in pyproject.toml alone would leave the platform on the
placeholder, which shrank the spleen and x-ray updates 20-200x until those tutorials stopped learning.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "fl-apps/flower/standard/pyproject.toml"
DP_TUTORIALS = sorted(
    path.parents[1]
    for path in (REPO / "fl-tutorials/flower").rglob("app/client_app.py")
    if "flip_local_dp_mod" in path.read_text()
)


def _app_config(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text())["tool"]["flwr"]["app"]["config"]


def test_the_dp_tutorials_are_found():
    assert len(DP_TUTORIALS) >= 4, DP_TUTORIALS


@pytest.mark.parametrize("tutorial", DP_TUTORIALS, ids=lambda p: p.name)
def test_platform_and_simulator_clip_to_the_same_norm(tutorial: Path):
    simulator = _app_config(tutorial / "pyproject.toml")["dp-clipping-norm"]
    config = tutorial / "app/config.toml"
    overrides = tomllib.loads(config.read_text()) if config.exists() else {}
    platform = overrides.get("dp-clipping-norm", _app_config(TEMPLATE)["dp-clipping-norm"])
    assert platform == simulator, (
        f"{tutorial.name}: the platform clips to {platform} but the simulator to {simulator}; "
        "set dp-clipping-norm in app/config.toml to the pyproject.toml value"
    )
