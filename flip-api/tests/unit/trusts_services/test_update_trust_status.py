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
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from flip_api.domain.schemas.trusts import UpdateTrustStatusSchema
from flip_api.main import app
from flip_api.trusts_services.update_trust_status import update_trust_status
from flip_api.utils.constants import SERVICE_UNAVAILABLE_MESSAGE

client = TestClient(app)

# Test data
user_id = "user-1"
model_id = "model-1"
trust_id = "trust-1"
trust_endpoint = "trust-a.ac.uk/flip"
fl_client_endpoint = "trust-a.ac.uk/flip:1111"
update_trust_status_data = UpdateTrustStatusSchema(fl_client_endpoint=fl_client_endpoint)
trust_status = "INITIALISED"


@pytest.fixture
def mock_get_session():
    with patch("flip_api.trusts_services.update_trust_status.get_session") as mock_get_session:
        mock_get_session.return_value = MagicMock()
        yield mock_get_session


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.state.user.sub = user_id
    return request


@pytest.fixture
def mock_can_access_model():
    with patch("flip_api.trusts_services.update_trust_status.can_access_model") as mock:
        mock.return_value = True
        yield mock


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_update_status_success(mock_mode, mock_request, mock_get_session):
    # Mock the db session
    mock_get_session.commit.return_value = None
    mock_get_session.execute.return_value.scalars.return_value.first.return_value = MagicMock(endpoint=trust_endpoint)

    response = update_trust_status(
        request=mock_request,
        model_id=model_id,
        trust_id=trust_id,
        trust_status=trust_status,
        data=update_trust_status_data,
        db=mock_get_session,
    )

    assert response["success"] == "message successfully sent"


def test_update_status_without_user_id(mock_request, mock_get_session):
    # Mock the db session
    mock_get_session.commit.return_value = None
    mock_get_session.execute.return_value.scalars.return_value.first.return_value = MagicMock(endpoint=trust_endpoint)

    # Simulate a request without user ID
    mock_request.state.user.sub = None

    response = update_trust_status(
        request=mock_request,
        model_id=model_id,
        trust_id=trust_id,
        trust_status=trust_status,
        data=update_trust_status_data,
        db=mock_get_session,
    )

    assert response["success"] == "message successfully sent"


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_update_status_invalid_status(mock_request, mock_get_session):
    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status="INVALID",  # invalid status
            data=update_trust_status_data,
            db=mock_get_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "not a valid status" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_update_status_missing_body(mock_mode, mock_request, mock_get_session):
    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=None,  # simulates missing body
            db=mock_get_session,
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        "Request body must be populated with fl_client_endpoint when status is set to INITIALISED"
        in exc_info.value.detail
    )


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=True)
def test_update_status_service_unavailable(mock_mode, mock_request, mock_get_session):
    """Simulate a service unavailable error when deployment mode is enabled"""
    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert SERVICE_UNAVAILABLE_MESSAGE in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_update_status_unauthorized(mock_mode, mock_request, mock_can_access_model, mock_get_session):
    # Simulate a user who cannot access the model
    mock_can_access_model.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert f"User with ID: {user_id} is denied access to this model" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_endpoint_does_not_exist_for_trust(mock_mode, mock_request, mock_get_session):
    # Mock DB session
    mock_get_session.execute.return_value.scalars.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert "Endpoint does not exist for trust" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.is_deployment_mode_enabled", return_value=False)
def test_update_status_error(mock_mode, mock_request, mock_can_access_model, mock_get_session):
    # Simulate an error during the update
    mock_get_session.commit.side_effect = Exception("Database error")

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert
    assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Error updating trust status: Database error" in exc_info.value.detail


# ------ Test cases for when the model or trust does not exist -------
# These tests check the behavior when either the model or trust does not exist in the database.


@patch("flip_api.trusts_services.update_trust_status.check_trust_exists", return_value=False)
@patch("flip_api.trusts_services.update_trust_status.check_model_exists", return_value=False)
def test_update_status_no_model_or_trust(
    mock_model_exists, mock_trust_exists, mock_request, mock_can_access_model, mock_get_session
):
    # Simulate no intersect
    mock_get_session.exec.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert that the correct 404 error is raised
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"Both {model_id=} and {trust_id=} do not exist" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.check_trust_exists", return_value=True)
@patch("flip_api.trusts_services.update_trust_status.check_model_exists", return_value=False)
def test_update_status_no_model(
    mock_model_exists, mock_trust_exists, mock_request, mock_can_access_model, mock_get_session
):
    # Simulate no intersect
    mock_get_session.exec.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert that the correct 404 error is raised for the missing model
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"{model_id=} does not exist" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.check_trust_exists", return_value=False)
@patch("flip_api.trusts_services.update_trust_status.check_model_exists", return_value=True)
def test_update_status_no_trust(
    mock_model_exists, mock_trust_exists, mock_request, mock_can_access_model, mock_get_session
):
    # Simulate no intersect
    mock_get_session.exec.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert that the correct 404 error is raised for the missing trust
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"{trust_id=} does not exist" in exc_info.value.detail


@patch("flip_api.trusts_services.update_trust_status.check_trust_exists", return_value=True)
@patch("flip_api.trusts_services.update_trust_status.check_model_exists", return_value=True)
def test_update_status_no_relationship(
    mock_model_exists, mock_trust_exists, mock_request, mock_can_access_model, mock_get_session
):
    # Simulate no intersect
    mock_get_session.exec.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        update_trust_status(
            request=mock_request,
            model_id=model_id,
            trust_id=trust_id,
            trust_status=trust_status,
            data=update_trust_status_data,
            db=mock_get_session,
        )

    # Assert that the correct 404 error is raised for no relationship between model and trust
    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert f"No relationship exists between {model_id=} and {trust_id=}" in exc_info.value.detail
