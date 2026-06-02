# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Shared log-policy fixtures for the pre-signed-URL unit tests.

Three test modules pin the same policy — no signed URL in a log line:

* ``tests/unit/utils/test_s3_client.py``
* ``tests/unit/file_services/test_presigned_url_for_upload.py``
* ``tests/unit/file_services/test_retrieve_federated_results.py``

The policy has three moving parts: the forbidden-token list, the SigV4
URL regex, and the assertion helper that walks a ``caplog`` record list
and inspects every channel a formatter could emit
(``getMessage()`` / ``args`` / ``exc_info`` / ``exc_text``). Centralise
them here so a new forbidden token or pattern is applied in one place,
and so all three modules use a byte-identical helper.

This is a plain module rather than a ``conftest.py``: the symbols are
imported by name (not pytest fixtures), and pytest's own docs steer
``conftest.py`` toward fixture/hook definitions only.
"""

import logging
import re

# A URL that mirrors a real SigV4 pre-signed URL. The mocked boto3
# client returns this so the assertions exercise the same query-string
# layout AWS would emit in production.
_FAKE_SIGNED_URL = (
    "https://test-bucket.s3.amazonaws.com/model-id/weights.bin?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=AKIAEXAMPLEKEY%2F20260506%2Feu-west-2%2Fs3%2Faws4_request&"
    "X-Amz-Date=20260506T000000Z&"
    "X-Amz-Expires=600&"
    "X-Amz-SignedHeaders=host&"
    "X-Amz-Signature=deadbeefcafe1234567890abcdef"
)

# Match any URL that carries SigV4 query parameters, regardless of host —
# AWS, moto/localstack (``http://localhost:4566/...``), GovCloud, S3
# Accelerate, and path-style endpoints all produce ``...?X-Amz-...`` URLs.
_S3_URL_WITH_QUERY = re.compile(r"https?://\S+\?\S*X-Amz-", re.IGNORECASE)

_FORBIDDEN_TOKENS = (
    "X-Amz-Signature=",
    "X-Amz-Credential=",
    "X-Amz-SignedHeaders=",
    "X-Amz-Security-Token=",
    "Authorization=AWS4-HMAC-SHA256",
)


def _assert_logs_have_no_presigned_url(records: list[logging.LogRecord]) -> None:
    """Assert that no captured log record carries pre-signed-URL material.

    Walks every channel a formatter could emit:

    * ``record.getMessage()`` — the formatted message
    * ``repr(record.args)`` — args supplied alongside a format string
    * ``record.exc_info`` — exception class / value, used by ``exc_info=True``
    * ``record.exc_text`` — cached formatted exception text

    Pinning the policy across all four channels means a future regression
    that re-adds ``exc_info=True`` with a URL-bearing exception will trip
    a test, not slip through the formatter into the application log.
    """
    for record in records:
        candidates = [record.getMessage()]
        if record.args:
            candidates.append(repr(record.args))
        if record.exc_info:
            _, exc_value, _ = record.exc_info
            candidates.append(repr(exc_value))
            candidates.append(str(exc_value))
        if record.exc_text:
            candidates.append(record.exc_text)
        for candidate in candidates:
            for token in _FORBIDDEN_TOKENS:
                assert token not in candidate, (
                    f"Pre-signed URL artefact {token!r} leaked into a log line: {candidate!r}"
                )
            assert not _S3_URL_WITH_QUERY.search(candidate), (
                f"Pre-signed S3 URL leaked into a log line: {candidate!r}"
            )
