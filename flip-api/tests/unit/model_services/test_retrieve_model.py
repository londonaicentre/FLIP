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

from unittest.mock import MagicMock, mock_open, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.schemas.status import FileUploadStatus, ModelStatus
from flip_api.main import app
from flip_api.model_services.retrieve_model import load_sql, retrieve_model

client = TestClient(app)

test_model_id = uuid4()
test_user_id = "user-123"

# ---------- Dependency Overrides ----------


@pytest.fixture(autouse=True)
def override_dependencies():
    mock_session = MagicMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[verify_token] = lambda: test_user_id
    yield mock_session
    app.dependency_overrides.clear()


# ---------- Patch Fixtures ----------


@pytest.fixture
def mock_can_access_true():
    with patch("flip_api.model_services.retrieve_model.can_access_model", return_value=True):
        yield


@pytest.fixture
def mock_can_access_false():
    with patch("flip_api.model_services.retrieve_model.can_access_model", return_value=False):
        yield


@pytest.fixture
def mock_load_sql():
    with patch("flip_api.model_services.retrieve_model.load_sql", return_value="SELECT * FROM fake_model_query"):
        yield


# ---------- Test Cases ----------


def test_retrieve_model_success(
    override_dependencies,
    mock_can_access_true,
    mock_load_sql,
):
    project_id = uuid4()
    query_id = uuid4()
    model_status = ModelStatus.PENDING.value
    file_id = str(uuid4())
    file_status = FileUploadStatus.COMPLETED.value

    mock_raw_sql_result = {
        "model_id": str(test_model_id),
        "model_name": "Test Model",
        "model_description": "Desc",
        "project_id": str(project_id),
        "status": model_status,
        "files": [
            {
                "id": file_id,
                "name": "file1.csv",
                "status": file_status,
                "size": 12345,
                "type": "csv",
                "tag": "training",
            }
        ],
        "query": {
            "id": str(query_id),
            "name": "Query A",
            "query": "SELECT *",
            "results": [
                {
                    "trust_name": "Trust One",
                    "data": {
                        "TotalCount": 100,
                        "Age": {"Mean": 45.6},
                        "Gender": {"Male": 60, "Female": 35, "MissingData": 5},
                        "ClientVisit": {"Emergency": 10, "Inpatient": 80, "MissingData": 10},
                    },
                }
            ],
        },
    }

    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = mock_raw_sql_result
    override_dependencies.execute.return_value = mock_result

    # response = client.get(f"/model/{test_model_id}")
    result = retrieve_model(model_id=test_model_id, db=override_dependencies, user_id=test_user_id)

    assert result.model_id == test_model_id
    assert result.model_name == "Test Model"
    assert result.project_id == project_id
    assert result.query.id == query_id
    assert len(result.files) == 1
    assert result.files[0].id == file_id
    assert result.files[0].status == FileUploadStatus.COMPLETED


def test_retrieve_model_forbidden(override_dependencies, mock_can_access_false):
    with pytest.raises(HTTPException) as exc:
        retrieve_model(model_id=test_model_id, db=override_dependencies, user_id=test_user_id)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert "denied access" in exc.value.detail


def test_retrieve_model_not_found(
    override_dependencies,
    mock_can_access_true,
    mock_load_sql,
):
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = None
    override_dependencies.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        retrieve_model(model_id=test_model_id, db=override_dependencies, user_id=test_user_id)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in exc.value.detail


def test_retrieve_model_database_error(
    override_dependencies,
    mock_can_access_true,
    mock_load_sql,
):
    override_dependencies.execute.side_effect = SQLAlchemyError

    with pytest.raises(HTTPException) as exc:
        retrieve_model(model_id=test_model_id, db=override_dependencies, user_id=test_user_id)
    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Database error" in exc.value.detail


def test_retrieve_model_unexpected_error(
    override_dependencies,
    mock_can_access_true,
):
    with patch("flip_api.model_services.retrieve_model.load_sql", side_effect=Exception("broken SQL file")):
        with pytest.raises(HTTPException) as exc:
            retrieve_model(model_id=test_model_id, db=override_dependencies, user_id=test_user_id)
        assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "broken SQL file" in exc.value.detail


def test_load_sql_reads_file_contents():
    dummy_sql = "SELECT * FROM model;"
    mocked_open = mock_open(read_data=dummy_sql)

    with patch("builtins.open", mocked_open):
        result = load_sql("query.sql")

    assert result == dummy_sql
    mocked_open.assert_called_once_with("query.sql", "r")
