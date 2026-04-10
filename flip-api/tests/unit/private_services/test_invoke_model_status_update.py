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
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from flip_api.domain.schemas.status import ModelStatus
from flip_api.private_services.invoke_model_status_update import (
    authenticate_internal_service,
    get_session,
)
from flip_api.private_services.invoke_model_status_update import router as invoke_model_status_update_router

test_app = FastAPI()
test_app.include_router(invoke_model_status_update_router, prefix="/api")

MOCKED_SERVICE_FUNCTION_PATH = "flip_api.private_services.invoke_model_status_update.update_model_status_endpoint"


@pytest.fixture
def client(mock_db_session: MagicMock):
    test_app.dependency_overrides[get_session] = lambda: mock_db_session
    test_app.dependency_overrides[authenticate_internal_service] = lambda: None
    return TestClient(test_app)


@pytest.fixture
def model_id():
    return uuid.uuid4()


class TestInvokeModelStatusUpdateEndpoint:
    @patch(MOCKED_SERVICE_FUNCTION_PATH)
    def test_invoke_update_success(
        self,
        mock_update_model: MagicMock,
        client: TestClient,
        model_id: UUID,
        mock_db_session: MagicMock,
    ):
        # Arrange
        model_status = ModelStatus.INITIATED.value
        service_response = {"success": "status set"}
        mock_update_model.return_value = service_response

        # Act
        response = client.put(f"/api/model/{model_id}/status/{model_status}")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == service_response
        mock_update_model.assert_called_once_with(
            model_id=model_id, model_status=ModelStatus.INITIATED, db=mock_db_session, user_id=None
        )

    @patch(MOCKED_SERVICE_FUNCTION_PATH)
    @patch("flip_api.private_services.invoke_model_status_update.logger.error")
    def test_invoke_update_service_raises_http_exception(
        self,
        mock_logger_error: MagicMock,
        mock_update_model: MagicMock,
        client: TestClient,
        model_id: UUID,
        mock_db_session: MagicMock,
    ):
        # Arrange
        model_status = ModelStatus.INITIATED.value
        error_detail = "Service-level validation failed"
        mock_update_model.side_effect = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

        # Act
        response = client.put(f"/api/model/{model_id}/status/{model_status}")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": error_detail}

    @patch(MOCKED_SERVICE_FUNCTION_PATH)
    @patch("flip_api.private_services.invoke_model_status_update.logger.error")
    def test_invoke_update_service_raises_general_exception(
        self,
        mock_logger_error: MagicMock,
        mock_update_model: MagicMock,
        client: TestClient,
        model_id: UUID,
        mock_db_session: MagicMock,
    ):
        # Arrange
        model_status = ModelStatus.INITIATED.value
        general_error = ValueError("Something went wrong in the service")
        mock_update_model.side_effect = general_error

        # Act
        response = client.put(f"/api/model/{model_id}/status/{model_status}")

        # Assert
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json() == {"detail": "An internal server error occurred while invoking model status update."}
        mock_logger_error.assert_called_once_with(
            f"Unhandled error in /model/{model_id}/status/{model_status}: {str(general_error)}",
            exc_info=True,
        )

    def test_invoke_update_unauthorized(self, model_id: UUID, mock_db_session: MagicMock):
        # Arrange
        def mock_auth():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        test_app.dependency_overrides[authenticate_internal_service] = mock_auth
        unauth_client = TestClient(test_app)

        model_status = ModelStatus.INITIATED.value

        # Act
        response = unauth_client.put(f"/api/model/{model_id}/status/{model_status}")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == {"detail": "Not authenticated"}

        # Clean up dependency override
        test_app.dependency_overrides.pop(authenticate_internal_service)
