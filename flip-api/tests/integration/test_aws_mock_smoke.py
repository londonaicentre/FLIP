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

"""Smoke tests for the moto scaffolding (B2 — issue #368).

These tests prove that, with the session-scoped ``aws_mock`` fixture active,
a vanilla ``boto3.client(...)`` constructed without an explicit ``endpoint_url``
reaches the moto fake. That's the wiring every B2 test downstream relies on:
production code in ``utils/s3_client.py``, ``utils/cognito_helpers.py`` and
the inline ``boto3.client("sesv2", ...)`` calls all build clients the same
way, with no test-only branch.

If either of these breaks the rest of B2 is meaningless, so failing loudly
here is the goal. The real per-service round-trips (S3 upload / Cognito
register / SES send through flip-api source) live in the dedicated
``test_s3_*`` / ``test_cognito_*`` / ``test_ses_*`` files.
"""

import boto3


def test_aws_mock_intercepts_s3_create_and_list_bucket():
    """A bare ``boto3.client('s3')`` should hit moto, not real AWS."""
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="b2-smoke-bucket")
    response = s3.list_buckets()

    assert "b2-smoke-bucket" in {b["Name"] for b in response["Buckets"]}


def test_aws_mock_intercepts_cognito_idp_create_user_pool():
    """``cognito-idp`` is the moto surface flip-api's user services depend on."""
    cognito = boto3.client("cognito-idp")
    pool = cognito.create_user_pool(PoolName="b2-smoke-pool")

    assert pool["UserPool"]["Name"] == "b2-smoke-pool"


def test_aws_mock_intercepts_sesv2_create_email_identity():
    """``sesv2`` (not legacy ``ses``) is what flip-api uses for outbound mail."""
    sesv2 = boto3.client("sesv2")
    sesv2.create_email_identity(EmailIdentity="smoke@example.com")
    response = sesv2.list_email_identities()

    assert "smoke@example.com" in {i["IdentityName"] for i in response["EmailIdentities"]}
