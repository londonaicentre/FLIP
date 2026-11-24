# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
#
"""The sender domain and the Cognito-through-SES switch stay opt-in (FLIP#592).

Both change who an email comes from, and each has a precondition outside this stack: the
domain's DKIM records are published by whoever runs its zone, and SES production access is an
AWS request. Applied early, the first stops FLIP's own emails and the second stops the
invitations and password resets that work on Cognito's built-in sender. So the defaults must be
today's behaviour, and the flip-api role must be able to send from the domain once it is used.
"""

from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parent.parent


def _source(relative: str) -> str:
    return strip_comments((AWS_DIR / relative).read_text())


def _default(source: str, variable: str) -> str:
    body = hcl_block(source, f'variable "{variable}"')
    return next(line.split("=", 1)[1].strip() for line in body.splitlines() if line.strip().startswith("default"))


def test_defaults_keep_the_current_senders() -> None:
    root = _source("variables.tf")
    assert _default(root, "SES_SENDER_EMAIL") == '""', "empty must mean: send from SES_VERIFIED_EMAIL"
    assert _default(root, "cognito_email_via_ses") == "false", "Cognito must keep its built-in sender by default"
    assert _default(_source("modules/cognito/variables.tf"), "ses_identity_arn") == '""'


def test_flip_api_sender_falls_back_to_the_verified_address() -> None:
    locals = " ".join(_source("locals.tf").split())
    assert 'ses_sender_email = var.SES_SENDER_EMAIL != "" ? var.SES_SENDER_EMAIL : var.SES_VERIFIED_EMAIL' in locals
    assert "AWS_SES_SENDER_EMAIL_ADDRESS = local.ses_sender_email" in locals
    assert "AWS_SES_ADMIN_EMAIL_ADDRESS = var.SES_VERIFIED_EMAIL" in locals


def test_cognito_uses_ses_only_behind_the_flag() -> None:
    cognito_call = hcl_block(_source("services.tf"), 'module "cognito"')
    assert 'var.cognito_email_via_ses ? module.ses.sender_domain_identity_arn : ""' in cognito_call
    pool = hcl_block(_source("modules/cognito/main.tf"), 'resource "aws_cognito_user_pool" "flip_user_pool"')
    email = hcl_block(pool, "email_configuration")
    assert 'var.ses_identity_arn == "" ? "COGNITO_DEFAULT" : "DEVELOPER"' in email


def test_flip_api_sends_as_the_one_sender_address_only() -> None:
    """A domain identity authorises any address on the domain; the role may use one."""
    policy = hcl_block(_source("iam_ecs.tf"), 'data "aws_iam_policy_document" "ecs_flip_api_task"')
    ses_send = " ".join(next(block for block in policy.split("statement {") if '"SesSend"' in block).split())
    assert 'var.SES_SENDER_EMAIL != "" ? [module.ses.sender_domain_identity_arn] : []' in ses_send, (
        "the domain identity is granted only once the sender is on the domain"
    )
    condition = " ".join(hcl_block(ses_send, "condition").split())
    assert 'test = "StringEquals"' in condition
    assert 'variable = "ses:FromAddress"' in condition
    assert "values = [local.ses_sender_email]" in condition


def test_makefile_exports_the_switches_only_when_set() -> None:
    makefile = (AWS_DIR / "Makefile").read_text()
    for key, export in (
        ("SES_SENDER_EMAIL", "TF_VAR_SES_SENDER_EMAIL"),
        ("COGNITO_EMAIL_VIA_SES", "TF_VAR_cognito_email_via_ses"),
    ):
        assert f"ifneq ($({key}),)\nexport {export}=${{{key}}}\nendif" in makefile, (
            f"{export} must be exported only when {key} is set, or an unset key overrides the variables.tf default"
        )
