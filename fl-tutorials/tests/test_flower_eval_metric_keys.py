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
# Drift guard (FLIP#928): Flower client apps must not pin metrics to a fixed ".x_<V>" coordinate.
#
# With best-model selection the evaluate phase runs every round, so a key like "test_loss.x_0" stacks
# one point per round at x=0. A bare key already plots at the server round it was measured in.
# Intra-round progress tagged with its own axis ("train_loss@epoch.x_5") is fine and not matched.

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_APPS = sorted(
    [*REPO_ROOT.glob("fl-tutorials/flower/**/client_app.py"), *REPO_ROOT.glob("fl-apps/flower/**/client_app.py")]
)
FIXED_X_KEY = re.compile(r"""\.x_0\b""")


def test_client_apps_found() -> None:
    assert CLIENT_APPS


@pytest.mark.parametrize("path", CLIENT_APPS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_fixed_x0_metric_keys(path: Path) -> None:
    assert not FIXED_X_KEY.search(path.read_text()), f"{path}: drop the '.x_0' twin, the bare key plots per round"
