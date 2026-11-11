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

"""The FL-server callback routes share one guard: no reporting job, no write."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.main import app

CALLBACKS = [
    ("PUT", "/api/model/{model_id}/status/ERROR", None),
    ("POST", "/api/model/{model_id}/logs", {"log": "line", "success": False}),
    (
        "POST",
        "/api/model/{model_id}/metrics",
        {"fl_client_name": "Trust_1", "global_round": 1, "label": "loss", "value": 0.5},
    ),
    ("POST", "/api/model/{model_id}/results-upload", {"file_name": "results.zip", "size": 1}),
    (
        "POST",
        "/api/model/{model_id}/results-upload/part",
        {"file_name": "results.zip", "upload_id": "u", "part_number": 1},
    ),
    (
        "POST",
        "/api/model/{model_id}/results-upload/complete",
        {"file_name": "results.zip", "upload_id": "u", "etags": ["e"]},
    ),
]


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[authenticate_internal_service] = lambda: None
    app.dependency_overrides[get_session] = lambda: MagicMock()
    yield TestClient(app)
    app.dependency_overrides = {}


@pytest.mark.parametrize(("method", "path", "body"), CALLBACKS)
def test_callback_without_a_reporting_job_changes_nothing(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    with (
        patch("flip_api.auth.job_callback.reporting_jobs", return_value=[]),
        patch("flip_api.private_services.invoke_model_status_update.update_model_status") as update_status,
        patch("flip_api.private_services.add_log.add_log") as add_log,
        patch("flip_api.private_services.save_training_metrics.save_training_metrics") as save_metrics,
        patch("flip_api.private_services.results_upload.S3Client") as s3_client,
    ):
        response = client.request(method, path.format(model_id=uuid4()), json=body)

    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorised for this model."}
    update_status.assert_not_called()
    add_log.assert_not_called()
    save_metrics.assert_not_called()
    s3_client.assert_not_called()
