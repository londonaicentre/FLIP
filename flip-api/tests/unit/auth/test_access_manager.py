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

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient

# Module to be tested
from flip_api.auth.access_manager import (
    API_KEY_HEADER_NAME,
    check_authorization_token,
)
from flip_api.config import Settings

# Dummy FastAPI app for testing the dependency
app_under_test = FastAPI()


@app_under_test.get("/secure-resource")
async def secure_resource_endpoint(token: str = Depends(check_authorization_token)):
    return {"message": "Access granted", "token_used": token}


client = TestClient(app_under_test)

VALID_TEST_KEY = "test_secret_key_12345_valid"
WRONG_TEST_KEY = "wrong_secret_key_67890_invalid"


@pytest.fixture
def mocked_settings():
    mock = Settings(
        PRIVATE_API_KEY=VALID_TEST_KEY,
    )
    with patch("flip_api.auth.access_manager.get_settings", return_value=mock):
        yield mock


@pytest.fixture
def mocked_settings_empty():
    mock = Settings(
        PRIVATE_API_KEY="",  # Simulating the case where the key is not set
    )
    with patch("flip_api.auth.access_manager.get_settings", return_value=mock):
        yield mock


class TestCheckAuthorizationToken:
    def test_valid_api_key_provided(self, mocked_settings):
        response = client.get("/secure-resource", headers={API_KEY_HEADER_NAME: VALID_TEST_KEY})
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"message": "Access granted", "token_used": VALID_TEST_KEY}

    def test_invalid_api_key_provided(self, mocked_settings):
        response = client.get("/secure-resource", headers={API_KEY_HEADER_NAME: WRONG_TEST_KEY})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == {"detail": "Invalid API Key."}
        assert response.headers.get("WWW-Authenticate") == "ApiKey"

    def test_missing_api_key_header(self):
        response = client.get("/secure-resource")  # No API key header
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == {"detail": "Not authenticated: API key is missing."}
        assert response.headers.get("WWW-Authenticate") == "ApiKey"

    def test_server_config_error_expected_key_not_set(self, mocked_settings_empty):
        # Ensure the environment variable is not set for this test
        response = client.get("/secure-resource", headers={API_KEY_HEADER_NAME: VALID_TEST_KEY})
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json() == {"detail": "Server configuration error: API key mechanism not set up."}

    # Direct function call tests (less critical if endpoint tests are comprehensive, but good for unit testing)
    def test_direct_call_valid_key(self, mocked_settings):
        returned_token = check_authorization_token(api_key=VALID_TEST_KEY)
        assert returned_token == VALID_TEST_KEY

    def test_direct_call_invalid_key(self):
        with pytest.raises(HTTPException) as exc_info:
            check_authorization_token(api_key=WRONG_TEST_KEY)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid API Key."

    def test_direct_call_missing_key_passed_as_none(self, mocked_settings):
        # This simulates how FastAPI's Security(api_key_header_scheme) would pass None
        # if the header is missing and auto_error=False.
        with pytest.raises(HTTPException) as exc_info:
            check_authorization_token(api_key=None)
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Not authenticated: API key is missing."

    def test_direct_call_server_config_error_key_not_set(self, mocked_settings_empty):
        with pytest.raises(HTTPException) as exc_info:
            check_authorization_token(api_key=VALID_TEST_KEY)
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc_info.value.detail == "Server configuration error: API key mechanism not set up."
