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

"""Unit tests for ``flip_api.utils.s3_client``.

Pins two contracts on ``S3Client.get_put_presigned_post``:

* the policy URL the method returns must never appear in a log line; and
* the TTL it requests from boto3 must satisfy the 600 s ceiling that
  ``MAX_PUT_PRESIGNED_URL_TTL_SECONDS`` encodes.

Together with the tests in
``tests/unit/file_services/test_presigned_url_for_upload.py`` and
``tests/unit/file_services/test_retrieve_federated_results.py``, this
module forms the policy retest required by the FLIP-PT review brief:
no log line may contain ``X-Amz-Signature=``, ``X-Amz-Credential=``,
or any ``s3.amazonaws.com/...?...`` URL.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from flip_api.utils.s3_client import MAX_PUT_PRESIGNED_URL_TTL_SECONDS, S3Client
from tests.unit._log_policy import _FAKE_SIGNED_URL, _assert_logs_have_no_presigned_url


@pytest.fixture
def s3_client_with_mock_boto():
    """Build an ``S3Client`` whose underlying boto3 client is a MagicMock.

    The wrapper class touches ``boto3.client(...)`` in ``__init__``, so we
    intercept the constructor first, then expose the wrapper plus the
    underlying mock for assertions.
    """
    with patch("flip_api.utils.s3_client.boto3.client") as mock_boto:
        boto_instance = MagicMock()
        mock_boto.return_value = boto_instance
        with patch(
            "flip_api.utils.s3_client.get_settings",
            return_value=MagicMock(AWS_REGION="us-east-1"),
        ):
            yield S3Client(), boto_instance


def test_get_put_presigned_post_passes_size_cap_into_conditions(s3_client_with_mock_boto):
    """The size cap must reach S3 as an explicit ``content-length-range``
    condition. Without this condition the policy is functionally identical
    to the unconstrained ``put_object`` URL we replaced.
    """
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {
        "url": "https://example.s3.amazonaws.com/",
        "fields": {"key": "uploads/123/weights.bin"},
    }

    s3.get_put_presigned_post(
        "s3://example/uploads/123/weights.bin",
        max_bytes=2048,
        content_type="application/octet-stream",
        expiration=600,
    )

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["Bucket"] == "example"
    assert kwargs["Key"] == "uploads/123/weights.bin"
    assert kwargs["ExpiresIn"] == 600
    assert ["content-length-range", 0, 2048] in kwargs["Conditions"]
    assert {"Content-Type": "application/octet-stream"} in kwargs["Conditions"]
    assert kwargs["Fields"]["Content-Type"] == "application/octet-stream"


def test_get_put_presigned_post_without_content_type_keeps_size_cap(s3_client_with_mock_boto):
    """A null Content-Type means "any type allowed", but the size cap must
    still be set. The policy without a size cap is the original DoS vector.
    """
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {
        "url": "https://example.s3.amazonaws.com/",
        "fields": {},
    }

    s3.get_put_presigned_post(
        "s3://example/uploads/123/weights.bin",
        max_bytes=4096,
        content_type=None,
    )

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert ["content-length-range", 0, 4096] in kwargs["Conditions"]
    # No Content-Type lock when the caller didn't supply one.
    assert all(
        not (isinstance(c, dict) and "Content-Type" in c) for c in kwargs["Conditions"]
    )
    assert "Content-Type" not in kwargs["Fields"]


def test_get_put_presigned_post_wraps_boto_clienterror(s3_client_with_mock_boto):
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}},
        "GeneratePresignedPost",
    )

    with pytest.raises(Exception, match="Unable to create a pre-signed POST policy"):
        s3.get_put_presigned_post(
            "s3://example/uploads/123/weights.bin",
            max_bytes=1024,
        )


def test_get_put_presigned_post_does_not_log_url(caplog, s3_client_with_mock_boto):
    """The success-path log line must not contain the policy URL."""
    caplog.set_level(logging.DEBUG, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {
        "url": _FAKE_SIGNED_URL,
        "fields": {"key": "uploads/123/weights.bin"},
    }

    s3.get_put_presigned_post(
        "s3://example/uploads/123/weights.bin",
        max_bytes=1024,
    )

    _assert_logs_have_no_presigned_url(caplog.records)


def test_get_put_presigned_post_caps_ttl_at_security_ceiling(s3_client_with_mock_boto):
    """A caller passing a permissive TTL must still get the security ceiling."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post(
        "s3://test-bucket/key",
        max_bytes=1024,
        expiration=3600,
    )

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PUT_PRESIGNED_URL_TTL_SECONDS
    assert kwargs["ExpiresIn"] <= 600


def test_get_put_presigned_post_default_ttl_is_at_most_600s(s3_client_with_mock_boto):
    """Default TTL must satisfy the 'TTL <= 600 s' policy requirement."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024)

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["ExpiresIn"] <= 600


def test_get_put_presigned_post_logs_warning_when_clamped(caplog, s3_client_with_mock_boto):
    """Over-ceiling callers must leave a warning trail so the silent clamp is auditable."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024, expiration=3600)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("3600" in r.getMessage() and "600" in r.getMessage() for r in warnings), (
        f"Expected a clamp warning citing both 3600s and 600s; got: {[r.getMessage() for r in warnings]}"
    )


def test_get_put_presigned_post_does_not_warn_at_or_below_ceiling(caplog, s3_client_with_mock_boto):
    """A within-policy caller must not trip the warning."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024, expiration=300)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, f"Did not expect a warning for in-policy TTL: {[r.getMessage() for r in warnings]}"


def test_get_put_presigned_post_at_ceiling_passes_through(s3_client_with_mock_boto):
    """Boundary case: an ``expiration`` exactly at the ceiling must round-trip unchanged."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post(
        "s3://test-bucket/key", max_bytes=1024, expiration=MAX_PUT_PRESIGNED_URL_TTL_SECONDS
    )

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PUT_PRESIGNED_URL_TTL_SECONDS


def test_max_put_presigned_url_ttl_is_600s():
    """Pin the ceiling value itself — moving it requires a security review."""
    assert MAX_PUT_PRESIGNED_URL_TTL_SECONDS == 600


def test_get_put_presigned_post_does_not_log_url_on_client_error(caplog, s3_client_with_mock_boto):
    """If boto raises ``ClientError``, the error log line must not contain the URL.

    ``ClientError`` from ``generate_presigned_post`` does not carry the URL today,
    but pin the contract so a future boto3 change cannot regress the redaction.
    """
    caplog.set_level(logging.DEBUG, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    # Simulate the worst case: an exception whose __str__ contains the URL.
    boto_instance.generate_presigned_post.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": _FAKE_SIGNED_URL}}, "generate_presigned_post"
    )

    with pytest.raises(Exception, match="Unable to create a pre-signed POST policy"):
        s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024)

    # The wrapped exception we re-raise has a static message, but the helper
    # also inspects the attached ``exc_info`` — if ``logger.exception`` were
    # ever reintroduced here, the boto ``ClientError`` (whose ``str()``
    # contains ``_FAKE_SIGNED_URL``) would land in the formatted traceback
    # and trip this assertion.
    _assert_logs_have_no_presigned_url(caplog.records)
