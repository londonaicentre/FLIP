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
from sqlalchemy.exc import SQLAlchemyError

from flip_api.domain.schemas.status import ModelStatus
from flip_api.private_services.invoke_model_status_update import (
    authenticate_internal_service,
    get_session,
)
from flip_api.private_services.invoke_model_status_update import router as invoke_model_status_update_router

test_app = FastAPI()
test_app.include_router(invoke_model_status_update_router, prefix="/api")

MOCKED_UPDATE_STATUS_PATH = "flip_api.private_services.invoke_model_status_update.update_model_status"
MOCKED_ADD_LOG_PATH = "flip_api.private_services.invoke_model_status_update.add_log"


@pytest.fixture
def client(mock_db_session: MagicMock):
    test_app.dependency_overrides[get_session] = lambda: mock_db_session
    test_app.dependency_overrides[authenticate_internal_service] = lambda: None
    return TestClient(test_app)


@pytest.fixture
def model_id():
    return uuid.uuid4()


class TestInvokeModelStatusUpdateEndpoint:
    @patch(MOCKED_ADD_LOG_PATH)
    @patch(MOCKED_UPDATE_STATUS_PATH)
    def test_invoke_update_success(
        self,
        mock_update: MagicMock,
        mock_add_log: MagicMock,
        client: TestClient,
        model_id: UUID,
        mock_db_session: MagicMock,
    ):
        mock_update.return_value = ModelStatus.INITIATED

        response = client.put(f"/api/model/{model_id}/status/{ModelStatus.INITIATED.value}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"success": "status set"}
        mock_update.assert_called_once_with(model_id, ModelStatus.INITIATED, mock_db_session)
        mock_add_log.assert_called_once()

    @patch(MOCKED_ADD_LOG_PATH)
    @patch(MOCKED_UPDATE_STATUS_PATH)
    def test_invoke_update_success_no_log(
        self,
        mock_update: MagicMock,
        mock_add_log: MagicMock,
        client: TestClient,
        model_id: UUID,
    ):
        mock_update.return_value = ModelStatus.PENDING

        response = client.put(f"/api/model/{model_id}/status/{ModelStatus.PENDING.value}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"success": "status set"}
        mock_add_log.assert_not_called()

    @patch(MOCKED_UPDATE_STATUS_PATH)
    def test_invoke_update_model_not_found(
        self,
        mock_update: MagicMock,
        client: TestClient,
        model_id: UUID,
    ):
        mock_update.return_value = None

        response = client.put(f"/api/model/{model_id}/status/{ModelStatus.INITIATED.value}")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "does not exist" in response.json()["detail"]

    @patch(MOCKED_UPDATE_STATUS_PATH)
    def test_invoke_update_database_error(
        self,
        mock_update: MagicMock,
        client: TestClient,
        model_id: UUID,
    ):
        mock_update.side_effect = SQLAlchemyError()

        response = client.put(f"/api/model/{model_id}/status/{ModelStatus.INITIATED.value}")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Database error" in response.json()["detail"]

    @patch(MOCKED_UPDATE_STATUS_PATH)
    @patch("flip_api.private_services.invoke_model_status_update.logger.error")
    def test_invoke_update_unexpected_error(
        self,
        mock_logger_error: MagicMock,
        mock_update: MagicMock,
        client: TestClient,
        model_id: UUID,
    ):
        general_error = ValueError("Something went wrong")
        mock_update.side_effect = general_error

        response = client.put(f"/api/model/{model_id}/status/{ModelStatus.INITIATED.value}")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in response.json()["detail"]

    def test_invoke_update_unauthorized(self, model_id: UUID, mock_db_session: MagicMock):
        def mock_auth():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

        test_app.dependency_overrides[authenticate_internal_service] = mock_auth
        unauth_client = TestClient(test_app)

        response = unauth_client.put(f"/api/model/{model_id}/status/{ModelStatus.INITIATED.value}")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json() == {"detail": "Not authenticated"}

        # Clean up dependency override
        test_app.dependency_overrides.pop(authenticate_internal_service)
