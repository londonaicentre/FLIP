#  Copyright 2026 London AI Centre
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Static guard: the flip-api task role grants every Cognito action flip-api calls (FLIP#1367).

``flip_api/auth/identity/cognito.py`` drives the user pool through a boto3 ``cognito-idp``
client, and the ``CognitoUserPool`` statement of ``data.aws_iam_policy_document.ecs_flip_api_task``
in ``iam_ecs.tf`` is what lets it. The two drifted once: ``admin_disable_user``,
``admin_enable_user`` and ``admin_set_user_mfa_preference`` were called but never granted, so
Disable User answered a 500 (AccessDenied) on every AWS estate. This test derives the required
actions from the ``client.<method>(`` calls (and the operations behind ``get_paginator``) so a
newly used Cognito call cannot ship without its IAM action.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AWS_PROVIDER_DIR.parents[2]
COGNITO_PY = REPO_ROOT / "flip-api" / "src" / "flip_api" / "auth" / "identity" / "cognito.py"
IAM_ECS_TF = AWS_PROVIDER_DIR / "iam_ecs.tf"

CLIENT_CALL = re.compile(r"\bclient\.([a-z][a-z0-9_]*)\(")
PAGINATOR_CALL = re.compile(r"\bclient\.get_paginator\(\s*[\"']([a-z][a-z0-9_]*)[\"']")

# Client methods that are boto3 helpers, not API operations (no IAM action of their own).
NON_API_METHODS = {"get_paginator", "get_waiter", "can_paginate", "close"}

# Operations whose IAM name is not the plain PascalCase of the snake_case method (acronyms).
ACTION_OVERRIDES = {
    "admin_set_user_mfa_preference": "AdminSetUserMFAPreference",
    "set_user_mfa_preference": "SetUserMFAPreference",
}

# Calls known to be in cognito.py today; the scan must find at least these, so a refactor that
# breaks the regex fails here rather than passing vacuously.
EXPECTED_SUBSET = {
    "cognito-idp:AdminDisableUser",
    "cognito-idp:AdminEnableUser",
    "cognito-idp:AdminSetUserMFAPreference",
    "cognito-idp:ListUsers",
}


def _iam_action(method: str) -> str:
    name = ACTION_OVERRIDES.get(method) or "".join(part.capitalize() for part in method.split("_"))
    return f"cognito-idp:{name}"


def _required_actions() -> set[str]:
    source = COGNITO_PY.read_text()
    methods = {m for m in CLIENT_CALL.findall(source) if m not in NON_API_METHODS}
    methods |= set(PAGINATOR_CALL.findall(source))
    return {_iam_action(m) for m in methods}


def _cognito_statement() -> str:
    source = strip_comments(IAM_ECS_TF.read_text())
    document = hcl_block(source, 'data "aws_iam_policy_document" "ecs_flip_api_task"')
    sid = re.search(r'^\s*sid\s*=\s*"CognitoUserPool"', document, re.MULTILINE)
    assert sid, "CognitoUserPool statement not found in ecs_flip_api_task"
    return hcl_block(document[document.rindex("statement", 0, sid.start()) :], "statement")


def _cognito_statement_actions() -> set[str]:
    actions = re.search(r"^\s*actions\s*=\s*\[(.*?)\]", _cognito_statement(), re.MULTILINE | re.DOTALL)
    assert actions, "CognitoUserPool statement has no actions list"
    return set(re.findall(r'"([^"]+)"', actions.group(1)))


@pytest.mark.parametrize(
    ("method", "action"),
    [
        ("admin_disable_user", "cognito-idp:AdminDisableUser"),
        ("admin_set_user_mfa_preference", "cognito-idp:AdminSetUserMFAPreference"),
        ("describe_user_pool_client", "cognito-idp:DescribeUserPoolClient"),
        ("list_users", "cognito-idp:ListUsers"),
    ],
)
def test_method_to_action_mapping(method: str, action: str) -> None:
    assert _iam_action(method) == action


def test_scan_finds_known_calls() -> None:
    missing = EXPECTED_SUBSET - _required_actions()
    assert not missing, f"cognito.py scan no longer finds {sorted(missing)}; fix the regex"


def test_cognito_statement_grants_every_called_action() -> None:
    missing = _required_actions() - _cognito_statement_actions()
    assert not missing, (
        f"flip-api calls Cognito operations its task role cannot perform: {sorted(missing)}. "
        "Add them to the CognitoUserPool statement in deploy/providers/AWS/iam_ecs.tf."
    )


def test_cognito_statement_stays_scoped_to_the_user_pool() -> None:
    resources = re.search(r"^\s*resources\s*=\s*\[(.*?)\]", _cognito_statement(), re.MULTILINE | re.DOTALL)
    assert resources
    assert resources.group(1).strip() == "module.cognito.user_pool_arn"
