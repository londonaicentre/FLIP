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

import base64
from unittest.mock import AsyncMock, MagicMock, patch

from imaging_api.routers.retrieval import base64_url_decode
from imaging_api.routers.schemas import ImportStatus
from imaging_api.utils.exceptions import NotFoundError


def test_base64_url_decode():
    original = "SELECT accession_id FROM cohort"
    encoded = base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")
    assert base64_url_decode(encoded) == original


def test_base64_url_decode_with_padding():
    original = "test"
    encoded = base64.urlsafe_b64encode(original.encode()).decode()
    assert base64_url_decode(encoded) == original


def _encode_query(query: str) -> str:
    return base64.urlsafe_b64encode(query.encode()).decode()


def test_get_import_status_count_success(client):
    mock_status = ImportStatus(
        successful=["ACC001", "ACC002"],
        failed=["ACC003"],
        processing=[],
        queued=["ACC004"],
        queue_failed=[],
    )

    with (
        patch("imaging_api.routers.retrieval.get_project", return_value=MagicMock()),
        patch(
            "imaging_api.routers.retrieval.get_import_status",
            new_callable=AsyncMock,
            return_value=mock_status,
        ),
    ):
        response = client.get(
            "/retrieval/import_status_count/PROJ1",
            params={"encoded_query": _encode_query("SELECT * FROM cohort")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["project_creation_completed"] is True
    status = data["import_status"]
    assert status["successful_count"] == 2
    assert status["failed_count"] == 1
    assert status["processing_count"] == 0
    assert status["queued_count"] == 1
    assert status["queue_failed_count"] == 0


def test_get_import_status_count_project_not_found(client):
    with patch(
        "imaging_api.routers.retrieval.get_project",
        side_effect=NotFoundError("Project not found"),
    ):
        response = client.get(
            "/retrieval/import_status_count/BAD_PROJ",
            params={"encoded_query": _encode_query("SELECT * FROM cohort")},
        )

    assert response.status_code == 404


def test_get_import_status_count_project_error(client):
    with patch(
        "imaging_api.routers.retrieval.get_project",
        side_effect=Exception("connection refused"),
    ):
        response = client.get(
            "/retrieval/import_status_count/PROJ1",
            params={"encoded_query": _encode_query("SELECT * FROM cohort")},
        )

    assert response.status_code == 500


def test_reimport_success(client):
    with (
        patch("imaging_api.routers.retrieval.get_settings") as mock_settings,
        patch("imaging_api.routers.retrieval.retry_retrieve_images_for_project", new_callable=AsyncMock),
    ):
        mock_settings.return_value = MagicMock(REIMPORT_STUDIES_ENABLED=True)

        response = client.put(
            "/retrieval/reimport_imaging_project_studies/PROJ1",
            params={"encoded_query": _encode_query("SELECT * FROM cohort")},
        )

    assert response.status_code == 202
    assert response.json()["message"] == "Reimport queued"
    assert response.json()["projectId"] == "PROJ1"


def test_reimport_disabled(client):
    with patch("imaging_api.routers.retrieval.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(REIMPORT_STUDIES_ENABLED=False)

        response = client.put(
            "/retrieval/reimport_imaging_project_studies/PROJ1",
            params={"encoded_query": _encode_query("SELECT * FROM cohort")},
        )

    assert response.status_code == 418
    assert "not enabled" in response.json()["detail"]
