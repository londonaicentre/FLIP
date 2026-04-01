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
import requests
from fastapi import HTTPException

from imaging_api.utils.xnat_token import XnatTokenFactory

URL = "http://localhost:8080"
USERNAME = "admin"
PASSWORD = "secret"


@pytest.fixture
def factory():
    return XnatTokenFactory(url=URL, username=USERNAME, password=PASSWORD)


class TestIsTokenValid:
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_valid_token(self, mock_get, factory):
        mock_get.return_value = MagicMock(status_code=200)
        assert factory.is_token_valid("valid-token") is True

    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_invalid_token(self, mock_get, factory):
        mock_get.return_value = MagicMock(status_code=401)
        assert factory.is_token_valid("expired-token") is False

    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_connection_error(self, mock_get, factory):
        mock_get.side_effect = requests.exceptions.ConnectionError("timeout")
        assert factory.is_token_valid("any-token") is False


class TestGetXnatCookie:
    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_fresh_token(self, mock_get, mock_post, factory):
        # No cached token → is_token_valid returns False → requests new one
        mock_get.return_value = MagicMock(status_code=401)
        mock_post.return_value = MagicMock(status_code=200, text="new-session-id")

        token = factory.get_xnat_cookie()

        assert token == "new-session-id"
        mock_post.assert_called_once_with(f"{URL}/data/JSESSION", auth=(USERNAME, PASSWORD))

    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_cached_valid_token(self, mock_get, factory):
        # Pre-populate cache
        factory.xnat_cookie = {"token": "cached-token"}
        mock_get.return_value = MagicMock(status_code=200)

        token = factory.get_xnat_cookie()

        assert token == "cached-token"

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_auth_failure_raises_http_exception(self, mock_get, mock_post, factory):
        mock_get.return_value = MagicMock(status_code=401)
        mock_post.return_value = MagicMock(status_code=403, text="Forbidden")

        with pytest.raises(HTTPException) as exc_info:
            factory.get_xnat_cookie()
        assert exc_info.value.status_code == 403

    @patch("imaging_api.utils.xnat_token.requests.post")
    @patch("imaging_api.utils.xnat_token.requests.get")
    def test_empty_response_raises_http_exception(self, mock_get, mock_post, factory):
        mock_get.return_value = MagicMock(status_code=401)
        mock_post.return_value = MagicMock(status_code=200, text="   ")

        with pytest.raises(HTTPException) as exc_info:
            factory.get_xnat_cookie()
        assert exc_info.value.status_code == 401
