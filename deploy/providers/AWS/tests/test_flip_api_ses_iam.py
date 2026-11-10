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
"""Static guard: the flip-api task role may use every SES template flip-api sends (FLIP#1840).

SES authorises a templated ``SendEmail`` against the template as well as the sender identity.
The ``SesSend`` statement of ``data.aws_iam_policy_document.ecs_flip_api_task`` once listed the
identity alone, so every email flip-api sent was denied on the template. The chain pinned here:
each ``*_TEMPLATE_NAME`` flip-api sends is declared in ``modules/ses``, every template declared
there is in the module's ``template_arns`` output, and ``SesSend`` grants that output.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AWS_PROVIDER_DIR.parents[2]
CONSTANTS_PY = REPO_ROOT / "flip-api" / "src" / "flip_api" / "utils" / "constants.py"
SES_MODULE = AWS_PROVIDER_DIR / "modules" / "ses"
IAM_ECS_TF = AWS_PROVIDER_DIR / "iam_ecs.tf"

TEMPLATE_NAME_CONSTANT = re.compile(r'^\w+_TEMPLATE_NAME\s*=\s*"([^"]+)"', re.MULTILINE)
TEMPLATE_RESOURCE = re.compile(r'^resource\s+"aws_ses_template"\s+"(\w+)"', re.MULTILINE)


def _sent_template_names() -> set[str]:
    return set(TEMPLATE_NAME_CONSTANT.findall(CONSTANTS_PY.read_text()))


def _ses_statement_resources() -> str:
    source = strip_comments(IAM_ECS_TF.read_text())
    document = hcl_block(source, 'data "aws_iam_policy_document" "ecs_flip_api_task"')
    sid = re.search(r'^\s*sid\s*=\s*"SesSend"', document, re.MULTILINE)
    assert sid, "SesSend statement not found in ecs_flip_api_task"
    statement = hcl_block(document[document.rindex("statement", 0, sid.start()) :], "statement")
    resources = re.search(r"^\s*resources\s*=\s*(.*)$", statement, re.MULTILINE)
    assert resources, "SesSend statement has no resources"
    return resources.group(1)


def test_scan_finds_the_known_templates() -> None:
    assert {"flip-access-request", "flip-xnat-invite"} <= _sent_template_names()


def test_every_template_flip_api_sends_is_declared_in_the_ses_module() -> None:
    module_source = strip_comments((SES_MODULE / "main.tf").read_text())
    missing = {name for name in _sent_template_names() if f'"{name}"' not in module_source}
    assert not missing, f"flip-api sends SES templates modules/ses does not declare: {sorted(missing)}"


def test_template_arns_output_lists_every_declared_template() -> None:
    templates = TEMPLATE_RESOURCE.findall(strip_comments((SES_MODULE / "main.tf").read_text()))
    assert templates, "no aws_ses_template found in modules/ses/main.tf; fix the regex"
    output = hcl_block(strip_comments((SES_MODULE / "outputs.tf").read_text()), 'output "template_arns"')
    missing = [name for name in templates if f"aws_ses_template.{name}.arn" not in output]
    assert not missing, f"modules/ses output template_arns omits {missing}; the task role cannot send them"


def test_ses_statement_grants_the_identity_and_the_templates() -> None:
    resources = _ses_statement_resources()
    assert "module.ses.sender_identity_arn" in resources
    assert "module.ses.template_arns" in resources, (
        "SesSend must grant the SES templates as well as the sender identity: "
        "a templated send is authorised against both (FLIP#1840)."
    )
