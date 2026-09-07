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

"""Unit tests for the Cognito-derived CORS allowlist (`flip_api.utils.cors`)."""

from unittest.mock import patch

import pytest

from flip_api.utils.cors import _origin_from_url, get_cors_allowed_origins


class TestOriginFromUrl:
    """Tests for the _origin_from_url normalizer used by the CORS allowlist builder."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # Default ports must be stripped — browsers omit them from the Origin header.
            ("https://localhost:443", "https://localhost"),
            ("http://example.com:80/path", "http://example.com"),
            # Non-default ports must be preserved.
            ("http://localhost:8080", "http://localhost:8080"),
            ("https://localhost:8443/", "https://localhost:8443"),
            # Path / query / fragment are dropped.
            ("https://app.flip.aicentre.co.uk/login?x=1#frag", "https://app.flip.aicentre.co.uk"),
            # Hostname is lowercased by urlparse.
            ("https://APP.FLIP.aicentre.co.uk", "https://app.flip.aicentre.co.uk"),
        ],
    )
    def test_normalizes_origin(self, url, expected):
        assert _origin_from_url(url) == expected

    @pytest.mark.parametrize("url", ["", "not-a-url", "/just/a/path"])
    def test_returns_none_for_unusable_input(self, url):
        assert _origin_from_url(url) is None


class TestGetCorsAllowedOrigins:
    """Tests for get_cors_allowed_origins (Cognito-derived CORS allowlist)."""

    @pytest.fixture
    def mock_boto3_client(self):
        with patch("flip_api.utils.cognito_helpers.boto3.client") as mock_client:
            yield mock_client

    @pytest.fixture
    def mock_settings(self):
        """Patch `get_settings` in *both* modules this path crosses.

        `get_cors_allowed_origins` reads the pool/client IDs via `cors.get_settings`,
        but the client it calls them on is built by `cognito_helpers._cognito_client`,
        which reads `AWS_REGION` via `cognito_helpers.get_settings`. Patching only one
        leaves the other resolving real environment config — green on a dev machine
        with a real `AWS_REGION`, red only in CI where it is the `<your-aws-region>`
        placeholder from `.env.development.example`.
        """
        with (
            patch("flip_api.utils.cors.get_settings") as mock_get_settings,
            patch("flip_api.utils.cognito_helpers.get_settings", mock_get_settings),
        ):
            settings = mock_get_settings.return_value
            settings.AWS_REGION = "eu-west-2"
            settings.AWS_COGNITO_USER_POOL_ID = "pool-id"
            settings.AWS_COGNITO_APP_CLIENT_ID = "client-id"
            yield mock_get_settings

    def test_returns_normalized_unique_origins(self, mock_boto3_client, mock_settings):
        """CallbackURLs are normalized to origins and deduplicated, preserving order."""
        mock_boto3_client.return_value.describe_user_pool_client.return_value = {
            "UserPoolClient": {
                "CallbackURLs": [
                    "https://app.flip.aicentre.co.uk",
                    "https://localhost:443",
                    # Duplicate after normalization (default port stripped) — must be deduped.
                    "https://app.flip.aicentre.co.uk/callback",
                ]
            }
        }

        origins = get_cors_allowed_origins()

        assert origins == ["https://app.flip.aicentre.co.uk", "https://localhost"]
        mock_boto3_client.assert_called_once_with("cognito-idp", region_name="eu-west-2")
        mock_boto3_client.return_value.describe_user_pool_client.assert_called_once_with(
            UserPoolId="pool-id", ClientId="client-id"
        )

    def test_returns_empty_list_when_no_callback_urls(self, mock_boto3_client, mock_settings):
        mock_boto3_client.return_value.describe_user_pool_client.return_value = {"UserPoolClient": {}}
        assert get_cors_allowed_origins() == []
