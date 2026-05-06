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

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from flip_api.utils.s3_client import S3Client


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
