# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status

from fl_api.app import app
from fl_api.core.dependencies import get_session
from fl_api.utils.job_resources import default_job_resources, parse_job_meta
from fl_api.utils.prepare_config import configure_client, configure_server, validate_config

# A valid Central Hub model_id (uuid4); the upload endpoint validates model_id as a UUID.
MODEL_ID = "c7f72374-0752-473f-a28f-592e4d8b7a47"


@pytest.fixture(autouse=True)
def override_session(client):
    """Override get_session with a conditional MagicMock that matches FLARE behavior."""
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
        "trusts": ["trustA", "trustB"],
        "bundle_urls": ["http://example.com/bundle1", "http://example.com/bundle2"],
    }


# --------------------------------------------------------------------------
# Tests for /upload_app/{model_id}
# --------------------------------------------------------------------------


def test_upload_app_success(client, override_session):
    """Should return 200 and response dict when upload succeeds."""
    with patch("fl_api.routers.application.upload_application", return_value={"status": "ok"}) as mock_upload:
        payload = make_upload_payload()
        response = client.post(f"/upload_app/{MODEL_ID}", json=payload)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

        # Validate call signature
        mock_upload.assert_called_once()
        args, kwargs = mock_upload.call_args
        assert args[0] == MODEL_ID  # model_id
        # body is a Pydantic model, compare its dict representation
        assert args[1].model_dump() == {**payload, "resources": None}
        assert kwargs["upload_dir"] == "/tmp/uploads"


def test_upload_app_passes_a_gpu_override_through(client, override_session):
    """The researcher's override at submission reaches upload_application as a JobResources (FLIP#70)."""
    with patch("fl_api.routers.application.upload_application", return_value={"status": "ok"}) as mock_upload:
        payload = {**make_upload_payload(), "resources": {"num_gpus": 1}}
        response = client.post(f"/upload_app/{MODEL_ID}", json=payload)

        assert response.status_code == status.HTTP_200_OK
        assert mock_upload.call_args.args[1].resources.model_dump() == {"num_gpus": 1}


@pytest.mark.parametrize(
    "resources",
    [{"num_gpus": -1}, {"num_gpus": "1"}, {"num_gpus": 1, "num_cpus": 2}, {"num_gpus": 1, "mem_per_gpu_gib": 7}],
)
def test_upload_app_rejects_a_malformed_gpu_override(client, override_session, resources):
    with patch("fl_api.routers.application.upload_application") as mock_upload:
        response = client.post(f"/upload_app/{MODEL_ID}", json={**make_upload_payload(), "resources": resources})

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        mock_upload.assert_not_called()


def test_upload_app_file_not_found(client):
    """Should return 404 when FileNotFoundError is raised."""
    with patch(
        "fl_api.routers.application.upload_application",
        side_effect=FileNotFoundError("missing file"),
    ):
        payload = make_upload_payload()
        response = client.post(f"/upload_app/{MODEL_ID}", json=payload)

        assert response.status_code == status.HTTP_404_NOT_FOUND


def test_upload_app_generic_error(client):
    """Should return 500 when an unexpected exception occurs."""
    with patch(
        "fl_api.routers.application.upload_application",
        side_effect=RuntimeError("unexpected crash"),
    ):
        payload = make_upload_payload()
        response = client.post(f"/upload_app/{MODEL_ID}", json=payload)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = response.json()
        assert "unexpected crash" in body["detail"].lower()


def test_upload_app_invalid_schema(client):
    """Should return 422 when request body doesn't match UploadAppRequest schema."""
    invalid_payload = {"project_id": "proj_001"}  # missing required fields
    response = client.post(f"/upload_app/{MODEL_ID}", json=invalid_payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_upload_app_rejects_non_uuid_model_id(client):
    """A non-UUID model_id is rejected by FastAPI's UUID path-param validation (422)."""
    response = client.post("/upload_app/not-a-uuid", json=make_upload_payload())
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


@pytest.mark.parametrize(
    "config",
    [
        {"AGGREGATOR": "unknown"},
        {"AGGREGATION_WEIGHTS": ["Trust_1"]},
        {"AGGREGATION_WEIGHTS": {"Trust_1": -1}},
        {"AGGREGATE_ONLY_REGEX": 12},
        {"AGGREGATE_ONLY_REGEX": "["},
        {"BEST_MODEL_METRIC": ""},
        {"BEST_MODEL_METRIC": "loss", "BEST_MODEL_METRIC_MINIMIZE": "yes"},
        {"BEST_MODEL_METRIC": "loss", "GLOBAL_ROUNDS": 1},
        {"BEST_MODEL_METRIC_MINIMIZE": True},
    ],
)
def test_upload_config_refusals_are_marked_for_researchers(client, config):
    with patch("fl_api.routers.application.upload_application", side_effect=lambda *a, **k: validate_config(config)):
        response = client.post(f"/upload_app/{MODEL_ID}", json=make_upload_payload())
    assert response.status_code == 400
    assert response.json()["refusal"] == response.json()["detail"]


@pytest.mark.parametrize("file", ["client", "server"])
def test_missing_required_config_is_marked_for_researchers(client, tmp_path, file):
    def configure(*args, **kwargs):
        if file == "client":
            return configure_client(tmp_path, MODEL_ID, "project", "SELECT 1")
        return configure_server(tmp_path, MODEL_ID, 1, ["Trust_1"], False, "unused", {})

    with patch("fl_api.routers.application.upload_application", side_effect=configure):
        response = client.post(f"/upload_app/{MODEL_ID}", json=make_upload_payload())
    assert response.status_code == 404
    assert f"config_fed_{file}.json" in response.json()["refusal"]
    assert str(tmp_path) not in response.json()["refusal"]


@pytest.mark.parametrize("failure", [ValueError("private /app/src"), FileNotFoundError("private /abs/file")])
def test_unmarked_exceptions_do_not_become_researcher_refusals(client, failure):
    with patch("fl_api.routers.application.upload_application", side_effect=failure):
        response = client.post(f"/upload_app/{MODEL_ID}", json=make_upload_payload())
    assert response.status_code in (400, 404)
    assert "refusal" not in response.json()


@pytest.mark.parametrize("meta", [{"resource_spec": {"@default": {"num_of_gpus": -1}}}, {"launcher_spec": {}}])
def test_invalid_job_metadata_is_marked_for_researchers(client, meta):
    with patch("fl_api.routers.application.upload_application", side_effect=lambda *a, **k: parse_job_meta(meta)):
        response = client.post(f"/upload_app/{MODEL_ID}", json=make_upload_payload())
    assert response.status_code == 400
    body = response.json()
    assert body["refusal"] == body["detail"]
    assert "meta.json" in body["refusal"]
    assert "num_of_gpus" in body["refusal"] or "launcher_spec" in body["refusal"]


def test_invalid_operator_gpu_default_is_not_a_researcher_refusal(client):
    with (
        patch("fl_api.utils.job_resources.get_settings", return_value=MagicMock(JOB_RESOURCE_SPEC_NUM_GPUS=99)),
        patch("fl_api.routers.application.upload_application", side_effect=lambda *a, **k: default_job_resources()),
    ):
        response = client.post(f"/upload_app/{MODEL_ID}", json=make_upload_payload())
    assert response.status_code == 400
    assert "JOB_RESOURCE_SPEC_NUM_GPUS" in response.json()["detail"]
    assert "refusal" not in response.json()


@pytest.mark.parametrize(
    "url",
    [
        (
            "https://user:userinfo-sentinel@bucket"  # pragma: allowlist secret (synthetic URL)
            "\uff0fexample.com/key?X-Amz-Signature=query-sentinel"
        ),
        (
            "https://user:userinfo-sentinel@[invalid/"  # pragma: allowlist secret (synthetic URL)
            "key?X-Amz-Signature=query-sentinel"
        ),
    ],
)
def test_upload_rejects_malformed_urls_without_echoing_credentials(client, override_session, tmp_path, caplog, url):
    caplog.set_level(logging.INFO)
    override_session.upload_dir = str(tmp_path)
    with patch("fl_api.utils.upload.requests.get") as fetch:
        response = client.post(f"/upload_app/{MODEL_ID}", json={**make_upload_payload(), "bundle_urls": [url]})
    assert response.status_code == 400
    assert response.json() == {"detail": "Bundle URL is malformed."}
    assert "userinfo-sentinel" not in response.text + caplog.text
    assert "query-sentinel" not in response.text + caplog.text
    fetch.assert_not_called()
