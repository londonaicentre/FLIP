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

Pins two contracts on both ``S3Client.get_put_presigned_post`` (upload) and
``S3Client.get_presigned_url`` (download):

* the policy/URL the method returns must never appear in a log line; and
* the TTL it requests from boto3 must satisfy the 1800 s ceiling that
  ``MAX_PRESIGNED_URL_TTL_SECONDS`` encodes — shared by both directions
  since a leaked URL is a capability against the bucket either way.

Together with the tests in
``tests/unit/file_services/test_presigned_url_for_upload.py``,
``tests/unit/file_services/test_download_file.py``, and
``tests/unit/file_services/test_retrieve_federated_results.py``, this
module forms the policy retest required by the FLIP-PT review brief:
no log line may contain ``X-Amz-Signature=``, ``X-Amz-Credential=``,
or any ``s3.amazonaws.com/...?...`` URL.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from flip_api.config import Settings
from flip_api.utils.s3_client import (
    _MULTIPART_OVERHEAD_BUFFER_BYTES,
    MAX_PRESIGNED_URL_TTL_SECONDS,
    S3Client,
)
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

    The cap sent to S3 is ``max_bytes + _MULTIPART_OVERHEAD_BUFFER_BYTES``
    because S3 measures the whole encoded request body, not just the file
    part.
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
    expected_cap = 2048 + _MULTIPART_OVERHEAD_BUFFER_BYTES
    assert ["content-length-range", 0, expected_cap] in kwargs["Conditions"]
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
    expected_cap = 4096 + _MULTIPART_OVERHEAD_BUFFER_BYTES
    assert ["content-length-range", 0, expected_cap] in kwargs["Conditions"]
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
    assert kwargs["ExpiresIn"] == MAX_PRESIGNED_URL_TTL_SECONDS


def test_get_put_presigned_post_default_ttl_is_at_most_ceiling(s3_client_with_mock_boto):
    """Default TTL must satisfy the 'TTL <= ceiling' policy requirement."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024)

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["ExpiresIn"] <= MAX_PRESIGNED_URL_TTL_SECONDS


def test_get_put_presigned_post_logs_warning_when_clamped(caplog, s3_client_with_mock_boto):
    """Over-ceiling callers must leave a warning trail so the silent clamp is auditable."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_post.return_value = {"url": "https://example/", "fields": {}}

    s3.get_put_presigned_post("s3://test-bucket/key", max_bytes=1024, expiration=3600)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("3600" in r.getMessage() and "1800" in r.getMessage() for r in warnings), (
        f"Expected a clamp warning citing both 3600s and 1800s; got: {[r.getMessage() for r in warnings]}"
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
        "s3://test-bucket/key", max_bytes=1024, expiration=MAX_PRESIGNED_URL_TTL_SECONDS
    )

    kwargs = boto_instance.generate_presigned_post.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PRESIGNED_URL_TTL_SECONDS


def test_max_presigned_url_ttl_is_1800s():
    """Pin the ceiling value itself — moving it requires a security review.

    Shared by both ``get_put_presigned_post`` (upload) and ``get_presigned_url``
    (download): a leaked URL is a capability against the bucket either way.
    """
    assert MAX_PRESIGNED_URL_TTL_SECONDS == 1800


def test_settings_default_ttl_equals_ceiling():
    """Pin PRE_SIGNED_URL_EXPIRATION_SECONDS' default to the ceiling: a higher
    default would be clamped anyway and make every default-configured
    deployment log the clamp warning on each presigned upload/download.
    """
    assert Settings.model_fields["PRE_SIGNED_URL_EXPIRATION_SECONDS"].default == MAX_PRESIGNED_URL_TTL_SECONDS


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


def test_get_presigned_url_passes_response_content_disposition(s3_client_with_mock_boto):
    """The Content-Disposition override must reach S3 as ResponseContentDisposition
    so the browser saves the file under the right name from a presigned GET.
    """
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url(
        "s3://example/models/123/weights.bin",
        expiration=600,
        response_content_disposition="attachment; filename=\"weights.bin\"",
    )

    kwargs = boto_instance.generate_presigned_url.call_args.kwargs
    assert kwargs["Params"]["ResponseContentDisposition"] == "attachment; filename=\"weights.bin\""
    assert kwargs["Params"]["Bucket"] == "example"
    assert kwargs["Params"]["Key"] == "models/123/weights.bin"
    assert kwargs["ExpiresIn"] == 600


def test_get_presigned_url_omits_response_content_disposition_when_not_given(s3_client_with_mock_boto):
    """A caller that doesn't need a filename override (e.g. federated results) must not
    have the param injected at all.
    """
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://example/results/123/metrics.json")

    kwargs = boto_instance.generate_presigned_url.call_args.kwargs
    assert "ResponseContentDisposition" not in kwargs["Params"]


def test_get_presigned_url_caps_ttl_at_security_ceiling(s3_client_with_mock_boto):
    """A caller passing a permissive TTL must still get the security ceiling."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://test-bucket/key", expiration=3600)

    kwargs = boto_instance.generate_presigned_url.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PRESIGNED_URL_TTL_SECONDS


def test_get_presigned_url_default_ttl_is_ceiling_without_warning(caplog, s3_client_with_mock_boto):
    """The function's default TTL must equal the ceiling AND not trip the clamp
    warning — a default-behaving caller is within policy, so warning on it would
    turn the clamp's audit trail into per-call noise.
    """
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://test-bucket/key")

    kwargs = boto_instance.generate_presigned_url.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PRESIGNED_URL_TTL_SECONDS
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, f"Default TTL must not warn: {[r.getMessage() for r in warnings]}"


def test_get_presigned_url_at_ceiling_passes_through(s3_client_with_mock_boto):
    """Boundary case: an ``expiration`` exactly at the ceiling must round-trip unchanged."""
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://test-bucket/key", expiration=MAX_PRESIGNED_URL_TTL_SECONDS)

    kwargs = boto_instance.generate_presigned_url.call_args.kwargs
    assert kwargs["ExpiresIn"] == MAX_PRESIGNED_URL_TTL_SECONDS


def test_get_presigned_url_logs_warning_when_clamped(caplog, s3_client_with_mock_boto):
    """Over-ceiling callers must leave a warning trail so the silent clamp is auditable."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://test-bucket/key", expiration=3600)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("3600" in r.getMessage() and "1800" in r.getMessage() for r in warnings), (
        f"Expected a clamp warning citing both 3600s and 1800s; got: {[r.getMessage() for r in warnings]}"
    )


def test_get_presigned_url_does_not_warn_at_or_below_ceiling(caplog, s3_client_with_mock_boto):
    """A within-policy caller must not trip the warning."""
    caplog.set_level(logging.WARNING, logger="uvicorn")
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.generate_presigned_url.return_value = "https://example.s3.amazonaws.com/signed"

    s3.get_presigned_url("s3://test-bucket/key", expiration=300)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, f"Did not expect a warning for in-policy TTL: {[r.getMessage() for r in warnings]}"


def test_upload_file_parses_path_and_uploads(s3_client_with_mock_boto):
    """upload_file parses the s3:// path and delegates to boto3's managed upload."""
    s3, boto_instance = s3_client_with_mock_boto

    s3.upload_file("/local/base/app/file1.py", "s3://dest-bucket/models/abc/app/file1.py")

    boto_instance.upload_file.assert_called_once_with(
        "/local/base/app/file1.py", "dest-bucket", "models/abc/app/file1.py"
    )


def test_upload_file_wraps_s3_upload_failed_error(s3_client_with_mock_boto):
    """A boto3 S3UploadFailedError (managed-transfer failure) is caught and re-raised wrapped.

    boto3's ``client.upload_file`` raises ``boto3.exceptions.S3UploadFailedError`` (not a botocore
    ClientError) on an S3-side failure such as AccessDenied on the destination bucket; the wrapper
    must still surface it as the tailored "Unable to upload file" error.
    """
    from boto3.exceptions import S3UploadFailedError

    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.upload_file.side_effect = S3UploadFailedError("Failed to upload: AccessDenied")

    with pytest.raises(Exception, match="Unable to upload file /local/f.py to s3://dest-bucket/k"):
        s3.upload_file("/local/f.py", "s3://dest-bucket/k")


# ---------- download_file / copy_object_if_match (#52 scan pipeline) ----------


def test_download_file_uses_managed_transfer(s3_client_with_mock_boto):
    s3, boto_instance = s3_client_with_mock_boto
    s3.download_file("s3://bucket/prefix/weights.pt", "/tmp/weights.pt")
    boto_instance.download_file.assert_called_once_with("bucket", "prefix/weights.pt", "/tmp/weights.pt")


def test_download_file_wraps_failures(s3_client_with_mock_boto):
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.download_file.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
    )
    with pytest.raises(Exception, match="Unable to download file"):
        s3.download_file("s3://bucket/prefix/weights.pt", "/tmp/weights.pt")


def test_copy_object_if_match_pins_the_etag(s3_client_with_mock_boto):
    """The scan pipeline's promote step depends on the ETag reaching S3 as a
    ``CopySourceIfMatch`` condition — without it, a re-uploaded (unscanned)
    object could be promoted on the previous object's verdict."""
    s3, boto_instance = s3_client_with_mock_boto
    s3.copy_object_if_match("s3://bucket/uploaded/k", "s3://bucket/scanned/k", '"etag"')
    boto_instance.copy.assert_called_once_with(
        {"Bucket": "bucket", "Key": "uploaded/k"},
        "bucket",
        "scanned/k",
        ExtraArgs={"CopySourceIfMatch": '"etag"'},
    )


def test_copy_object_if_match_raises_precondition_error(s3_client_with_mock_boto):
    from flip_api.utils.s3_client import S3PreconditionFailedError

    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.copy.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "At least one of the pre-conditions..."}},
        "CopyObject",
    )
    with pytest.raises(S3PreconditionFailedError):
        s3.copy_object_if_match("s3://bucket/uploaded/k", "s3://bucket/scanned/k", '"etag"')


def test_copy_object_if_match_wraps_other_failures(s3_client_with_mock_boto):
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.copy.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "CopyObject"
    )
    with pytest.raises(Exception, match="Unable to copy object"):
        s3.copy_object_if_match("s3://bucket/uploaded/k", "s3://bucket/scanned/k", '"etag"')


def test_delete_object_if_match_sends_the_precondition(s3_client_with_mock_boto):
    """The quarantine delete must carry IfMatch so S3 evaluates it atomically —
    a read-then-delete would let a re-upload slip in between and be destroyed
    on the previous object's scan verdict."""
    s3, boto_instance = s3_client_with_mock_boto
    s3.delete_object_if_match("s3://bucket/uploaded/k", '"etag"')
    boto_instance.delete_object.assert_called_once_with(
        Bucket="bucket", Key="uploaded/k", IfMatch='"etag"'
    )


def test_delete_object_if_match_raises_precondition_error(s3_client_with_mock_boto):
    from flip_api.utils.s3_client import S3PreconditionFailedError

    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.delete_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "..."}}, "DeleteObject"
    )
    with pytest.raises(S3PreconditionFailedError):
        s3.delete_object_if_match("s3://bucket/uploaded/k", '"etag"')


def test_delete_object_if_match_wraps_other_failures(s3_client_with_mock_boto):
    s3, boto_instance = s3_client_with_mock_boto
    boto_instance.delete_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DeleteObject"
    )
    with pytest.raises(Exception, match="Unable to delete object"):
        s3.delete_object_if_match("s3://bucket/uploaded/k", '"etag"')
