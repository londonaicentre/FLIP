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
"""Every az call targets the one subscription the run checked (FLIP#1390).

`az --subscription ""` silently falls back to the CLI default, which on the build account is not
the trial. So the subscription id is read once per make run, and the run stops unless it is a
subscription id. These tests run the real targets with az stubbed on PATH.
"""

from __future__ import annotations

import pytest
from conftest import run_make


def test_one_run_reads_the_subscription_once(tmp_path):
    result, calls = run_make(tmp_path, "status")

    assert result.returncode == 0, result.stdout + result.stderr
    assert len([c for c in calls if c.startswith("account show")]) == 1, calls
    assert any(c.startswith("vm run-command") for c in calls)


@pytest.mark.parametrize("answer", ["", "WARNING: the default subscription has changed", "not-a-guid"])
def test_an_answer_that_is_not_a_subscription_id_stops_the_run(tmp_path, answer):
    result, calls = run_make(tmp_path, "status", account_id=answer)

    assert result.returncode != 0
    assert "Cannot read subscription 'trial'" in result.stdout + result.stderr
    assert all(c.startswith("account show") for c in calls), "no other az call ran"
