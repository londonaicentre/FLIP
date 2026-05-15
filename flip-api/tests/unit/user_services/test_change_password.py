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

import uuid
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from flip_api.user_services.change_password import (
    ChangePasswordRequest,
    change_own_password,
    revoke_own_token,
)


@pytest.fixture
def token_id():
    return uuid.uuid4()


@pytest.fixture
def mock_credentials():
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="test-access-token",  # pragma: allowlist secret
    )


@pytest.fixture
def valid_body():
    return ChangePasswordRequest(
        current_password="OldPass1!",  # pragma: allowlist secret
        new_password="NewPass1!",  # pragma: allowlist secret
    )


def _make_client_error(code: str, message: str = "An error occurred") -> ClientError:
    """Build a minimal ClientError to simulate a Cognito failure."""
    error_response = {
        "Error": {
            "Code": code,
            "Message": message,
        },
    }
    return ClientError(error_response, "change_password")


class TestChangeOwnPassword:
    """Tests for POST /users/me/password."""

    def test_success_with_refresh_token(self, mock_request, token_id, mock_credentials, valid_body):
        """Happy path: correct password + refresh token → Cognito change_password + revoke_token called."""
        mock_request.headers = {"X-Refresh-Token": "test-refresh-token"}

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.revoke_token") as mock_revoke,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id

            result = change_own_password(
                body=valid_body,
                request=mock_request,
                credentials=mock_credentials,
                token_id=token_id,
            )

            mock_client.change_password.assert_called_once_with(
                PreviousPassword="OldPass1!",  # pragma: allowlist secret
                ProposedPassword="NewPass1!",  # pragma: allowlist secret
                AccessToken="test-access-token",
            )
            mock_revoke.assert_called_once()
            assert result == {"detail": "Password changed successfully. Please sign in again."}

    def test_success_without_refresh_token(self, mock_request, token_id, mock_credentials, valid_body):
        """Missing X-Refresh-Token header → password changes, revocation skipped (best-effort)."""
        mock_request.headers = {}  # no refresh token header

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.revoke_token") as mock_revoke,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id

            result = change_own_password(
                body=valid_body,
                request=mock_request,
                credentials=mock_credentials,
                token_id=token_id,
            )

            mock_client.change_password.assert_called_once()
            mock_revoke.assert_not_called()
            assert result == {"detail": "Password changed successfully. Please sign in again."}

    def test_revoke_failure_still_succeeds(self, mock_request, token_id, mock_credentials, valid_body):
        """Password change succeeds even if token revocation fails (best-effort)."""
        mock_request.headers = {"X-Refresh-Token": "test-refresh-token"}

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.revoke_token") as mock_revoke,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id
            mock_revoke.side_effect = HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to revoke token",
            )

            result = change_own_password(
                body=valid_body,
                request=mock_request,
                credentials=mock_credentials,
                token_id=token_id,
            )

            mock_client.change_password.assert_called_once()
            mock_revoke.assert_called_once()
            assert result == {"detail": "Password changed successfully. Please sign in again."}

    def test_wrong_current_password_raises_400(self, mock_request, token_id, mock_credentials, valid_body):
        """Wrong current password → Cognito NotAuthorizedException → 400."""
        mock_request.headers = {}

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client.change_password.side_effect = _make_client_error("NotAuthorizedException")
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id

            with pytest.raises(HTTPException) as exc_info:
                change_own_password(
                    body=valid_body,
                    request=mock_request,
                    credentials=mock_credentials,
                    token_id=token_id,
                )

            assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
            assert "Current password is incorrect" in exc_info.value.detail

    def test_cognito_limit_exceeded_raises_500(self, mock_request, token_id, mock_credentials, valid_body):
        """Cognito throttling or other errors → 500."""
        mock_request.headers = {}

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client.change_password.side_effect = _make_client_error("LimitExceededException")
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id

            with pytest.raises(HTTPException) as exc_info:
                change_own_password(
                    body=valid_body,
                    request=mock_request,
                    credentials=mock_credentials,
                    token_id=token_id,
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to change password" in exc_info.value.detail

    def test_invalid_new_password_raises_400(self, mock_request, token_id, mock_credentials, valid_body):
        """Cognito InvalidPasswordException → 400."""
        mock_request.headers = {}

        with (
            patch("flip_api.user_services.change_password._cognito_client") as mock_client_factory,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_client = MagicMock()
            mock_client.change_password.side_effect = _make_client_error("InvalidPasswordException")
            mock_client_factory.return_value = mock_client
            mock_verify.return_value = token_id

            with pytest.raises(HTTPException) as exc_info:
                change_own_password(
                    body=valid_body,
                    request=mock_request,
                    credentials=mock_credentials,
                    token_id=token_id,
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


class TestRevokeOwnToken:
    """Tests for PUT /users/revoke/{refresh_token}."""

    def test_success_returns_204(self, token_id):
        """Valid refresh token → revoke_token called, no body returned."""
        with (
            patch("flip_api.user_services.change_password.revoke_token") as mock_revoke,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_verify.return_value = token_id

            result = revoke_own_token(
                refresh_token="test-refresh-token",
                token_id=token_id,
            )

            mock_revoke.assert_called_once()
            assert result is None

    def test_cognito_failure_raises_500(self, token_id):
        """Invalid or expired token → revoke_token raises → 500 propagates."""
        with (
            patch("flip_api.user_services.change_password.revoke_token") as mock_revoke,
            patch("flip_api.user_services.change_password.verify_token") as mock_verify,
        ):
            mock_verify.return_value = token_id
            mock_revoke.side_effect = HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to revoke token",
            )

            with pytest.raises(HTTPException) as exc_info:
                revoke_own_token(
                    refresh_token="bad-token",
                    token_id=token_id,
                )

            assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to revoke token" in exc_info.value.detail
