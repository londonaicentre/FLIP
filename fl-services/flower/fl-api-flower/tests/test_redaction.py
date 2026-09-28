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
        ("X-Trust-Internal-Service-Key: abc123def456", "abc123def456"),  # pragma: allowlist secret
        ("AWS_SECRET_ACCESS_KEY=notARealKeyJustAShape", "notARealKeyJustAShape"),  # pragma: allowlist secret
        ('{"api_key": "sk-live-9999"}', "sk-live-9999"),  # pragma: allowlist secret
        ("password=hunter2", "hunter2"),
        ("Bearer token=eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ("credentials AKIANOTAREALKEYSHAPE in env", "AKIANOTAREALKEYSHAPE"),  # pragma: allowlist secret
        # The repo's own naming puts a suffix after the keyword: `AES_KEY_BASE64`,
        # `INTERNAL_SERVICE_KEY_HASH`. A keyword that must end at the separator misses both.
        ("AES_KEY_BASE64=c2VjcmV0LWtleS1ieXRlcw==", "c2VjcmV0LWtleS1ieXRlcw=="),  # pragma: allowlist secret
        ("INTERNAL_SERVICE_KEY_HASH=9f86d081884c7d65", "9f86d081884c7d65"),  # pragma: allowlist secret
        # Authorization headers carry no keyword before the separator.
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",  # pragma: allowlist secret
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0",  # pragma: allowlist secret
        ),
        ("Authorization: Basic dXNlcjpodW50ZXIy", "dXNlcjpodW50ZXIy"),  # pragma: allowlist secret
        # The SigV4 *header* form (boto3 debug logging prints it): `Signature=` is not a
        # query parameter, so the URL rule does not see it.
        (
            "Authorization: AWS4-HMAC-SHA256 "
            "Credential=AKIANOTAREALKEYSHAPE/20260914/eu-west-2/s3/aws4_request, "  # pragma: allowlist secret
            "SignedHeaders=host, Signature=deadbeefcafe0123",
            "deadbeefcafe0123",  # pragma: allowlist secret
        ),
        # A presigned URL whose separators were HTML- or JSON-escaped by whatever logged it.
        ("https://b.s3.amazonaws.com/k?a=1&amp;X-Amz-Signature=deadbeefcafe", "deadbeefcafe"),
        ("https://b.s3.amazonaws.com/k?a=1\\u0026X-Amz-Signature=deadbeefcafe", "deadbeefcafe"),
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
    assert "AKIANOTAREALKEYSHAPE" not in redacted  # pragma: allowlist secret
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
        # A keyword suffix is bounded, so an ordinary word that merely contains a keyword
        # does not drag the rest of the line in.
        "monkeypatch.setenv('FLOWER_RUN_LOG_MAX_CHARS', '120')",
    ],
)
def test_leaves_diagnostic_text_intact(text):
    # Over-redaction is tolerated, but not at the cost of the lines that name the cause.
    assert redact_secrets(text) == text
