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

import pytest

from fl_api.utils.redaction import REDACTED, redact_secrets


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("X-Internal-Service-Key: abc123def456", "abc123def456"),  # pragma: allowlist secret
        ("X-Trust-Internal-Service-Key: abc123def456", "abc123def456"),
        ("AWS_SECRET_ACCESS_KEY=notARealKeyJustAShape", "notARealKeyJustAShape"),  # pragma: allowlist secret
        ('{"api_key": "sk-live-9999"}', "sk-live-9999"),
        ("password=hunter2", "hunter2"),
        ("Bearer token=eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ("credentials AKIANOTAREALKEYSHAPE in env", "AKIANOTAREALKEYSHAPE"),  # pragma: allowlist secret
    ],
)
def test_redacts_credential_shapes(text, secret):
    redacted = redact_secrets(text)

    assert secret not in redacted
    assert REDACTED in redacted


def test_redacts_each_sigv4_parameter_without_eating_the_url():
    url = (
        "https://bucket.s3.eu-west-2.amazonaws.com/model/app.py"
        "?X-Amz-Credential=AKIANOTAREALKEYSHAPE%2F20260819"  # pragma: allowlist secret
        "&X-Amz-Signature=deadbeefcafe&X-Amz-Expires=900"
    )

    redacted = redact_secrets(url)

    assert "deadbeefcafe" not in redacted
    assert "AKIANOTAREALKEYSHAPE" not in redacted
    # The object path and the non-secret parameters stay readable — they are the
    # diagnostically useful half of the line.
    assert "model/app.py" in redacted
    assert "X-Amz-Expires=900" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "ImportError: cannot import name 'min_clients_from_run_config' from 'flip.flower.strategy'",
        "KeyError: 'flip-cohort-query'",
        "ERROR: Exit Code: 607",
        "Traceback (most recent call last):",
    ],
)
def test_leaves_diagnostic_text_intact(text):
    # Over-redaction is tolerated, but not at the cost of the lines that name the cause.
    assert redact_secrets(text) == text
