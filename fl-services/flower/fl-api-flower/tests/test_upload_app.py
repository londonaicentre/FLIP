# Copyright (c) 2026 Flower Labs GmbH
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

from unittest.mock import Mock

import pytest
from tomlkit import parse

from fl_api.schemas import UploadAppRequest


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """Base directory the FL API downloads bundles into (FLOWER_SRC_ROOT)."""
    upload_dir = tmp_path / "src"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOWER_SRC_ROOT", str(upload_dir))
    return upload_dir


@pytest.fixture
def mock_requests_get(monkeypatch):
    """Mock requests.get to simulate file downloads."""

    def _mock_get(url_to_content: dict[str, bytes]):
        def _get(url, stream=True, timeout=60):
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            if url in url_to_content:
                content = url_to_content[url]
                mock_response.iter_content = Mock(return_value=[content])
            else:
                mock_response.raise_for_status.side_effect = Exception(f"URL not found: {url}")

            # Support context manager protocol (with statement)
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            return mock_response

        import fl_api.utils.upload as upload_module

        monkeypatch.setattr(upload_module.requests, "get", _get)

    return _mock_get


def test_upload_app_basic_success(client, upload_dir, mock_requests_get):
    """Test basic app upload with pyproject.toml and config.toml."""
    model_id = "test-model-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    config_content = b"""
[models.my_model]
path = "model_path"
checkpoint = "model.pt"
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/app/config.toml": config_content,
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="SELECT * FROM patients",
        trusts=["trust1", "trust2"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/app/config.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200
    result = response.json()
    assert "successfully" in result["message"].lower()

    # Verify files were created
    job_dir = upload_dir / model_id
    assert (job_dir / "pyproject.toml").exists()
    assert (job_dir / "app" / "config.toml").exists()


def test_upload_app_injects_flip_params_without_touching_pyproject(client, upload_dir, mock_requests_get):
    """config.toml receives the FLIP params; pyproject.toml is left untouched."""
    model_id = "test-inject-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    config_content = b"""
[models.my_model]
path = "unet"
checkpoint = "model.pt"

[metrics]
accuracy = true
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/app/config.toml": config_content,
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/app/config.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    job_dir = upload_dir / model_id

    # pyproject.toml is left as uploaded -- no models/metrics merged into it
    pyproject_doc = parse((job_dir / "pyproject.toml").read_text())
    config_section = pyproject_doc["tool"]["flwr"]["app"]["config"]
    assert "num_server_rounds" in config_section
    assert "models" not in config_section
    assert "metrics" not in config_section

    # config.toml gets the FLIP params and keeps the researcher's original content
    config_doc = parse((job_dir / "app" / "config.toml").read_text())
    assert config_doc["flip-model-id"] == model_id
    assert config_doc["flip-project-id"] == "project-123"
    assert config_doc["flip-cohort-query"] == "*"
    assert config_doc["models"]["my_model"]["path"] == "unet"
    assert "metrics" in config_doc


def test_upload_app_places_checkpoint_in_job_dir(client, upload_dir, mock_requests_get):
    """Checkpoint files are downloaded into the job dir alongside the sources.

    They are no longer routed to a separate volume: the evaluation ServerApp reads
    them from the job dir via the injected flip-job-dir run-config value.
    """
    model_id = "test-checkpoint-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/model.pt": b"fake model weights",
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/model.pt",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    job_dir = upload_dir / model_id
    assert (job_dir / "model.pt").exists()
    assert (job_dir / "pyproject.toml").exists()


def test_upload_app_keeps_config_toml_in_app_dir(client, upload_dir, mock_requests_get):
    """config.toml stays in app/ (where submit_run reads it); it is not moved to the job root."""
    model_id = "test-config-location-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    app_config_content = b"""
[models.my_model]
path = "model_path"
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/app/config.toml": app_config_content,
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/app/config.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    job_dir = upload_dir / model_id

    # config.toml stays in app/
    assert (job_dir / "app" / "config.toml").exists()

    # it is not moved to the job root
    assert not (job_dir / "config.toml").exists()


def test_upload_app_download_failure(client, upload_dir, mock_requests_get):
    """Test that upload fails gracefully when file download fails."""
    model_id = "test-download-fail-123"

    # Mock only one URL, making the second fail
    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": b"content",
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/missing.toml",  # This will fail
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 500


def test_upload_app_cleans_existing_directories(client, upload_dir, mock_requests_get):
    """Test that an existing job directory is cleaned before upload."""
    model_id = "test-clean-123"

    # Create an existing job directory with an old file
    job_dir = upload_dir / model_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old_file.txt").write_text("old content")

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
    })

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    # Old file should be gone
    assert not (job_dir / "old_file.txt").exists()

    # New file should exist
    assert (job_dir / "pyproject.toml").exists()
