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

"""Static checks on the Cognito module's variables.tf.

Pins the contract that admin and researcher seed users have distinct,
sensitive, optional password variables — and that the old shared variable is
fully retired.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VARIABLES_TF = Path(__file__).parent / "variables.tf"


@pytest.fixture(scope="module")
def variables_tf_text() -> str:
    return VARIABLES_TF.read_text()


def _extract_variable_block(source: str, name: str) -> str:
    pattern = rf'variable\s+"{re.escape(name)}"\s*\{{'
    match = re.search(pattern, source)
    assert match, f"variable {name!r} not found"

    depth = 1
    i = match.end()
    while i < len(source) and depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"unbalanced braces in variable {name!r}"
    return source[match.end() : i - 1]


class TestSharedVariableRemoved:
    def test_seed_user_password_variable_removed(self, variables_tf_text: str) -> None:
        # The single source of the shared-credential bug. Must not come back.
        assert not re.search(r'variable\s+"seed_user_password"', variables_tf_text)


class TestPerUserPasswordVariables:
    @pytest.mark.parametrize("name", ["admin_user_password", "researcher_user_password"])
    def test_variable_exists(self, variables_tf_text: str, name: str) -> None:
        assert re.search(rf'variable\s+"{name}"', variables_tf_text), f"variable {name!r} missing"

    @pytest.mark.parametrize("name", ["admin_user_password", "researcher_user_password"])
    def test_variable_is_sensitive(self, variables_tf_text: str, name: str) -> None:
        body = _extract_variable_block(variables_tf_text, name)
        assert re.search(r"sensitive\s*=\s*true", body), f"variable {name!r} not marked sensitive = true"

    @pytest.mark.parametrize("name", ["admin_user_password", "researcher_user_password"])
    def test_variable_defaults_to_null(self, variables_tf_text: str, name: str) -> None:
        # A null default is what lets Cognito generate a one-time temp
        # password and deliver it through the invite template instead of
        # leaving a known shared value baked into state.
        body = _extract_variable_block(variables_tf_text, name)
        assert re.search(r"default\s*=\s*null", body), f"variable {name!r} must default to null"

    @pytest.mark.parametrize("name", ["admin_user_password", "researcher_user_password"])
    def test_variable_is_string_typed(self, variables_tf_text: str, name: str) -> None:
        body = _extract_variable_block(variables_tf_text, name)
        assert re.search(r"type\s*=\s*string", body), f"variable {name!r} must be type = string"
