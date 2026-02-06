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
from fastapi import status

from fl_api.app import app
from fl_api.core.dependencies import get_session


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches NVFLARE behavior."""
    fake_session = MagicMock()

    # --- Application methods ---
    fake_session.upload_dir = "/tmp/uploads"
    fake_session.get_available_apps_to_upload.return_value = ["app1", "app2"]

    app.dependency_overrides[get_session] = lambda: fake_session
    yield fake_session  # ✅ yield it so tests can access it
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Helper: build a valid UploadAppRequest payload
# --------------------------------------------------------------------------


def make_upload_payload():
    """Return a fully valid UploadAppRequest JSON body."""
    return {
        "project_id": "proj_001",
        "cohort_query": "SELECT * FROM patients WHERE site='A'",
        "local_rounds": 2,
        "global_rounds": 10,
        "trusts": ["trustA", "trustB"],
        "bundle_urls": ["http://example.com/bundle1", "http://example.com/bundle2"],
        "ignore_result_error": False,
        "aggregator": "InTimeAccumulateWeightedAggregator",
        "aggregation_weights": {"trustA": 0.6, "trustB": 0.4},
    }


# --------------------------------------------------------------------------
# Tests for /upload_app/{model_id}
# --------------------------------------------------------------------------


def test_upload_app_success(client, override_session):
    """Should return 200 and response dict when upload succeeds."""
    with patch("fl_api.routers.application.upload_application", return_value={"status": "ok"}) as mock_upload:
        payload = make_upload_payload()
        response = client.post("/upload_app/model123", json=payload)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Validate call signature
        mock_upload.assert_called_once()
        args, kwargs = mock_upload.call_args
        assert args[0] == "model123"  # model_id
        # body is a Pydantic model, compare its dict representation
        assert args[1].model_dump() == payload
        assert kwargs["upload_dir"] == "/tmp/uploads"


def test_upload_app_file_not_found(client):
    """Should return 404 when FileNotFoundError is raised."""
    with patch(
        "fl_api.routers.application.upload_application",
        side_effect=FileNotFoundError("missing file"),
    ):
        payload = make_upload_payload()
        response = client.post("/upload_app/model123", json=payload)

        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_upload_app_generic_error(client):
    """Should return 500 when an unexpected exception occurs."""
    with patch(
        "fl_api.routers.application.upload_application",
        side_effect=RuntimeError("unexpected crash"),
    ):
        payload = make_upload_payload()
        response = client.post("/upload_app/model123", json=payload)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = response.json()
        assert "unexpected crash" in body["detail"].lower()


def test_upload_app_invalid_schema(client):
    """Should return 422 when request body doesn't match UploadAppRequest schema."""
    invalid_payload = {"project_id": "proj_001"}  # missing required fields
    response = client.post("/upload_app/model123", json=invalid_payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# --------------------------------------------------------------------------
# Tests for /get_available_apps_to_upload
# --------------------------------------------------------------------------


def test_get_available_apps_success(client, override_session):
    """Should return list of app names."""
    response = client.get("/get_available_apps_to_upload")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body == ["app1", "app2"]
    override_session.get_available_apps_to_upload.assert_called_once()


def test_get_available_apps_error(client, override_session):
    """Should return 500 when session raises an exception."""
    override_session.get_available_apps_to_upload.side_effect = Exception("disk error")

    response = client.get("/get_available_apps_to_upload")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    body = response.json()
    assert "disk error" in body["detail"].lower()
