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
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.auth.dependencies import verify_token
from flip_api.db.models.user_models import UsersAudit
from flip_api.domain.schemas.users import Disabled
from flip_api.main import app  # Ensure your FastAPI app includes the /users router
from flip_api.user_services.update_user import get_session

client = TestClient(app)

# -------------------
# Fixtures & Mocks
# -------------------


@pytest.fixture
def sample_user_id():
    return uuid4()


@pytest.fixture
def caller_id():
    return uuid4()


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture(autouse=True)
def override_dependencies(mock_session, caller_id):
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: caller_id
    yield
    app.dependency_overrides.clear()


# -------------------
# Tests
# -------------------
class TestUpdateUser:
    @patch("flip_api.user_services.update_user.update_xnat_user_profile")
    @patch("flip_api.user_services.update_user.update_user")
    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_disable_writes_audit(
        self,
        mock_has_permissions,
        mock_get_username,
        mock_update_user,
        mock_update_xnat,
        sample_user_id,
        mock_session,
        caller_id,
    ):
        """Disable branch: Cognito + XNAT mutate, then a 'Disabled user' audit row is written."""
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "testuser@example.com"
        mock_update_user.return_value = Disabled(disabled=True)

        response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"disabled": True}
        mock_update_user.assert_called_once()
        mock_update_xnat.assert_called_once()

        # Audit row was written with the correct action + actor.
        mock_session.add.assert_called_once()
        audit_row = mock_session.add.call_args[0][0]
        assert isinstance(audit_row, UsersAudit)
        assert audit_row.action == "Disabled user"
        assert audit_row.user_id == sample_user_id
        assert audit_row.modified_by_user_id == caller_id
        mock_session.commit.assert_called_once()

    @patch("flip_api.user_services.update_user.update_xnat_user_profile")
    @patch("flip_api.user_services.update_user.update_user")
    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_enable_writes_audit(
        self,
        mock_has_permissions,
        mock_get_username,
        mock_update_user,
        mock_update_xnat,
        sample_user_id,
        mock_session,
        caller_id,
    ):
        """Enable branch: action string is 'Enabled user'."""
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "testuser@example.com"
        mock_update_user.return_value = Disabled(disabled=False)

        response = client.put(f"/api/users/{sample_user_id}", json={"disabled": False})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"disabled": False}
        mock_session.add.assert_called_once()
        audit_row = mock_session.add.call_args[0][0]
        assert audit_row.action == "Enabled user"

    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_forbidden(self, mock_has_permissions, sample_user_id):
        mock_has_permissions.return_value = False

        response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "unable to manage users" in response.json()["detail"]

    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_not_found(self, mock_has_permissions, mock_get_username, sample_user_id):
        """If the Cognito sub is gone, get_username raises 404 and that propagates unchanged."""
        mock_has_permissions.return_value = True
        mock_get_username.side_effect = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {sample_user_id} is not registered.",
        )

        response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "is not registered" in response.json()["detail"]

    @patch("flip_api.user_services.update_user.get_username", side_effect=Exception("Unexpected error"))
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_update_user_exception(self, mock_has_permissions, mock_get_username, sample_user_id):
        """Catch-all returns a generic detail; raw exception text is not leaked."""
        mock_has_permissions.return_value = True

        response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["detail"] == "Failed to update user"
        # Sanity: the exception text didn't leak into the response.
        assert "Unexpected error" not in response.json()["detail"]

    @patch("flip_api.user_services.update_user.update_xnat_user_profile")
    @patch("flip_api.user_services.update_user.update_user")
    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_xnat_failure_after_cognito_succeeds_surfaces_partial_state(
        self,
        mock_has_permissions,
        mock_get_username,
        mock_update_user,
        mock_update_xnat,
        sample_user_id,
        mock_session,
    ):
        """Cognito mutated successfully; the XNAT profile update then raised.

        The Cognito state has changed but XNAT and the audit row haven't —
        the response detail must name the partial state explicitly so the
        operator can reconcile, instead of surfacing a generic
        "Failed to update user" that's indistinguishable from "nothing
        happened".
        """
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "testuser@example.com"
        mock_update_user.return_value = Disabled(disabled=True)
        mock_update_xnat.side_effect = Exception("XNAT queue write failed")

        with patch("flip_api.user_services.update_user.logger") as mock_logger:
            response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        detail = response.json()["detail"].lower()
        assert "cognito" in detail
        assert "xnat" in detail
        assert "partially" in detail or "verify" in detail
        # Audit row was NOT written — XNAT failed before that step.
        mock_session.add.assert_not_called()
        mock_logger.exception.assert_called()

    @patch("flip_api.user_services.update_user.update_xnat_user_profile")
    @patch("flip_api.user_services.update_user.update_user")
    @patch("flip_api.user_services.update_user.get_username")
    @patch("flip_api.user_services.update_user.has_permissions")
    def test_audit_commit_failure_after_cognito_succeeds_surfaces_500(
        self,
        mock_has_permissions,
        mock_get_username,
        mock_update_user,
        mock_update_xnat,
        sample_user_id,
        mock_session,
    ):
        """Cognito + XNAT mutated successfully; the audit-row commit then failed.

        The user-visible state has changed, so 500 is the right signal — but
        the detail must not echo SQLAlchemy text and the failure must be
        logged at exception level for forensic reconciliation.
        """
        mock_has_permissions.return_value = True
        mock_get_username.return_value = "testuser@example.com"
        mock_update_user.return_value = Disabled(disabled=True)
        mock_session.commit.side_effect = Exception("DB unavailable")

        with patch("flip_api.user_services.update_user.logger") as mock_logger:
            response = client.put(f"/api/users/{sample_user_id}", json={"disabled": True})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "DB unavailable" not in response.json()["detail"]
        mock_session.rollback.assert_called_once()
        mock_logger.exception.assert_called()
