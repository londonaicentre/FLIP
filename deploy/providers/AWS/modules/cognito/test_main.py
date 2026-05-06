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

"""Static checks on the Cognito module's main.tf.

The module seeds two Cognito users (admin + researcher). They must not share a
credential, and the resource definitions must be resilient to subsequent
applies that flip the password value to/from null.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAIN_TF = Path(__file__).parent / "main.tf"


@pytest.fixture(scope="module")
def main_tf_text() -> str:
    return MAIN_TF.read_text()


def _extract_resource_block(source: str, resource_type: str, resource_name: str) -> str:
    """Return the body of `resource "<type>" "<name>" { ... }`.

    Walks braces to find the matching close so nested blocks (lifecycle,
    attributes, dynamic) don't trip the regex.
    """
    pattern = rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(resource_name)}"\s*\{{'
    match = re.search(pattern, source)
    assert match, f"resource {resource_type}.{resource_name} not found"

    depth = 1
    i = match.end()
    while i < len(source) and depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"unbalanced braces in {resource_type}.{resource_name}"
    return source[match.end() : i - 1]


class TestSeedUserPasswordsAreDistinct:
    def test_seed_user_password_variable_is_gone(self, main_tf_text: str) -> None:
        # The shared variable was the root cause — both seed users used to
        # reference the same value.
        assert "var.seed_user_password" not in main_tf_text

    def test_admin_uses_admin_password_variable(self, main_tf_text: str) -> None:
        body = _extract_resource_block(main_tf_text, "aws_cognito_user", "admin_user")
        assert re.search(r"\bpassword\s*=\s*var\.admin_user_password\b", body)
        assert "var.researcher_user_password" not in body
        assert "var.seed_user_password" not in body

    def test_researcher_uses_researcher_password_variable(self, main_tf_text: str) -> None:
        body = _extract_resource_block(main_tf_text, "aws_cognito_user", "researcher_user")
        assert re.search(r"\bpassword\s*=\s*var\.researcher_user_password\b", body)
        assert "var.admin_user_password" not in body
        assert "var.seed_user_password" not in body

    def test_admin_and_researcher_reference_distinct_variables(self, main_tf_text: str) -> None:
        admin = _extract_resource_block(main_tf_text, "aws_cognito_user", "admin_user")
        researcher = _extract_resource_block(main_tf_text, "aws_cognito_user", "researcher_user")
        admin_pw = re.search(r"password\s*=\s*(var\.\w+)", admin)
        researcher_pw = re.search(r"password\s*=\s*(var\.\w+)", researcher)
        assert admin_pw is not None
        assert researcher_pw is not None
        assert admin_pw.group(1) != researcher_pw.group(1)


class TestPasswordIgnoreChanges:
    """Both seed users must list `password` in lifecycle.ignore_changes.

    Without this, a value→null transition (when an operator decides to stop
    pinning a known seed password) becomes a forced replacement, which the
    prevent_destroy lifecycle then blocks. The two arms must move together.
    """

    @pytest.mark.parametrize("resource_name", ["admin_user", "researcher_user"])
    def test_password_in_ignore_changes(self, main_tf_text: str, resource_name: str) -> None:
        body = _extract_resource_block(main_tf_text, "aws_cognito_user", resource_name)
        lifecycle_match = re.search(r"lifecycle\s*\{([^}]*ignore_changes\s*=\s*\[[^\]]*\][^}]*)\}", body, re.DOTALL)
        assert lifecycle_match, f"{resource_name} missing lifecycle.ignore_changes"
        ignore_match = re.search(r"ignore_changes\s*=\s*\[([^\]]*)\]", lifecycle_match.group(1), re.DOTALL)
        assert ignore_match is not None
        items = [item.strip() for item in ignore_match.group(1).split(",") if item.strip()]
        assert "password" in items, f"{resource_name} ignore_changes missing 'password': {items}"
        assert "enabled" in items, f"{resource_name} ignore_changes missing 'enabled': {items}"

    @pytest.mark.parametrize("resource_name", ["admin_user", "researcher_user"])
    def test_prevent_destroy_still_set(self, main_tf_text: str, resource_name: str) -> None:
        body = _extract_resource_block(main_tf_text, "aws_cognito_user", resource_name)
        assert re.search(r"prevent_destroy\s*=\s*true", body), f"{resource_name} lost prevent_destroy"
