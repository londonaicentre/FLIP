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
"""The node's and the cluster's targets moved to vm/ and aks/ (FLIP#1787).

Run from the tree's own Makefile, an old name fails and names the command that replaced it, and
nothing reaches Azure or Terraform.
"""

from __future__ import annotations

import pytest
from conftest import run_make
from test_static_guards import TARGETS


@pytest.mark.parametrize(
    ("old", "new"),
    [
        *[(t, f"make -C trust/deploy/terraform/azure/vm {t}") for t in TARGETS["vm"] if t != "help"],
        *[(f"aks-{t}", f"make -C trust/deploy/terraform/azure/aks {t}") for t in TARGETS["aks"] if t != "help"],
    ],
)
def test_an_old_name_fails_and_names_the_new_command(tmp_path, old, new):
    result, calls = run_make(tmp_path, old)

    assert result.returncode != 0
    assert new in result.stdout + result.stderr
    assert not calls, "nothing ran"
