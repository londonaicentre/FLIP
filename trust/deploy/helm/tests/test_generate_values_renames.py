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
"""The env-var renames in ``generate_values.py`` are announced, not silent.

Dropping a name from ``ENV_VAR_MAP`` is silent by construction: the variable
stops reaching the generated values and the chart default applies instead. For a
data-version pin that is a *changed deployed dataset* rather than an error, and
nothing in the install would report it — which is why ``RENAMED_ENV_VARS``
exists and why it is checked here rather than left to a reader of the diff.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[1]
_SCRIPT = CHART_DIR / "scripts" / "generate_values.py"
_spec = importlib.util.spec_from_file_location("generate_values", _SCRIPT)
generate_values = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_values)


def test_the_old_pin_is_no_longer_read():
    overrides, _ = generate_values.build_values({"OMOP_DATA_VERSION": "20260729"})

    assert "trustData" not in overrides


def test_the_old_pin_being_set_is_reported(capsys):
    generate_values.build_values({"OMOP_DATA_VERSION": "20260729"})

    warning = capsys.readouterr().err
    assert "OMOP_DATA_VERSION" in warning
    assert "TRUST_DATA_VERSION" in warning
    assert "20260729" in warning


def test_the_new_pin_maps_through(capsys):
    overrides, _ = generate_values.build_values({"TRUST_DATA_VERSION": "20260901"})

    assert overrides["trustData"]["version"] == "20260901"
    assert capsys.readouterr().err == ""


def test_the_new_pin_wins_and_says_so_when_both_are_set(capsys):
    overrides, _ = generate_values.build_values({"OMOP_DATA_VERSION": "20260729", "TRUST_DATA_VERSION": "20260901"})

    assert overrides["trustData"]["version"] == "20260901"
    assert "in effect" in capsys.readouterr().err


def test_every_rename_target_is_a_name_the_script_actually_reads():
    """A rename pointing at a name nothing maps would send operators to a dead variable."""
    unmapped = set(generate_values.RENAMED_ENV_VARS.values()) - set(generate_values.ENV_VAR_MAP)

    assert unmapped == set(), f"RENAMED_ENV_VARS points at names ENV_VAR_MAP does not carry: {sorted(unmapped)}"


def test_no_rename_target_is_itself_retired():
    overlap = set(generate_values.RENAMED_ENV_VARS) & set(generate_values.ENV_VAR_MAP)

    assert overlap == set(), f"these are both retired and mapped: {sorted(overlap)}"
