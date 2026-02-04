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
from fastapi.testclient import TestClient

from imaging_api.main import app
from imaging_api.routers.schemas import ImportStudy, ImportStudyRequest, ImportStudyResponse, PacsStatus, Patient, Study
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.exceptions import NotFoundError

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_headers():
    app.dependency_overrides[get_xnat_auth_headers] = lambda: {"Authorization": "Bearer fake-token"}
    yield
    app.dependency_overrides.clear()


def test_ping_pacs_success():
    pacs_id = 1
    mock_response = {
        "pacsId": pacs_id,
        "successful": True,
        "pingTime": 123,
        "created": 1610000000,
        "enabled": True,
        "timestamp": 1610001234,
        "id": 1,
        "disabled": 0,
    }

    with patch("imaging_api.routers.imaging.ping_pacs") as mock_ping_pacs:
        mock_ping_pacs.return_value = PacsStatus(**mock_response)

        response = client.get(f"/imaging/ping_pacs/{pacs_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["pacsId"] == pacs_id
        assert data["successful"] is True
        assert data["enabled"] is True
        assert "created" in data
        assert "timestamp" in data
        assert "pingTime" in data


def test_ping_pacs_failure():
    pacs_id = 2
    error_message = f"PACS with ID '{pacs_id}' not found."

    with patch("imaging_api.routers.imaging.ping_pacs") as mock_ping_pacs:
        mock_ping_pacs.side_effect = NotFoundError(error_message)

        response = client.get(f"/imaging/ping_pacs/{pacs_id}")

        assert response.status_code == 404
        assert error_message in response.json()["detail"]


def test_query_by_accession_number_success():
    mock_study = Study(
        studyInstanceUid="1.2.3",
        studyDescription="Head MRI",
        accessionNumber="ACC123",
        studyDate="2023-01-01",
        modalitiesInStudy=["MR"],
        referringPhysicianName="Dr. Smith",
        patient=Patient(
            id="NHS 123456",
            name="John Doe",
            sex="M",
        ),
    )

    with patch("imaging_api.routers.imaging.query_by_accession_number", return_value=[mock_study]):
        response = client.get(
            "/imaging/query_by_accession_number",
            params={"accession_number": "ACC123"},
        )

    assert response.status_code == 200
    assert response.json()[0]["accessionNumber"] == "ACC123"


def test_query_by_accession_number_not_found():
    accession_number = "ACC123"
    message = f"No studies found for accession number: {accession_number}"

    with patch(
        "imaging_api.routers.imaging.query_by_accession_number",
        side_effect=NotFoundError(message),
    ):
        response = client.get(
            "/imaging/query_by_accession_number",
            params={"accession_number": accession_number},
        )

    assert response.status_code == 404
    assert message in response.json()["detail"]


def test_queue_image_import_request_success():
    import_request = ImportStudyRequest(
        projectId="TEST123",
        pacsId=1,
        studies=[
            ImportStudy(
                studyInstanceUid="1.2.3",
                accessionNumber="ACC123",
            )
        ],
    )

    mock_response = [
        ImportStudyResponse(
            id=1,
            pacsId=1,
            status="QUEUED",
            accessionNumber="ACC123",
            queuedTime=1234567890,
            created=1234567890,
            priority=1,
        )
    ]

    with patch("imaging_api.routers.imaging.queue_image_import_request", return_value=mock_response):
        response = client.post(
            "/imaging/queue_image_import_request",
            json=import_request.model_dump(by_alias=True),
        )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "QUEUED"


def test_queue_image_import_request_not_found():
    import_request = ImportStudyRequest(
        projectId="TEST123",
        pacsId=1,
        studies=[
            ImportStudy(
                studyInstanceUid="1.2.3",
                accessionNumber="ACC123",
            )
        ],
    )

    with patch(
        "imaging_api.routers.imaging.queue_image_import_request",
        side_effect=NotFoundError("No studies found on PACS with the study instance UID(s) provided"),
    ):
        response = client.post(
            "/imaging/queue_image_import_request",
            json=import_request.model_dump(by_alias=True),
        )

    assert response.status_code == 404
    assert "No studies found on PACS with the study instance UID(s) provided" in response.json()["detail"]
