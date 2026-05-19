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
def upload_dir(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


@pytest.fixture
def checkpoints_dir(tmp_path):
    checkpoints_dir = tmp_path / "model_checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    return checkpoints_dir


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


def test_upload_app_basic_success(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
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

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

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
    assert result["job_type"] == "standard"

    # Verify files were created
    job_dir = upload_dir / model_id
    assert (job_dir / "pyproject.toml").exists()
    assert (job_dir / "config.toml").exists()


def test_upload_app_merges_config_into_pyproject(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
    """Test that config.toml sections are merged into pyproject.toml [tool.flwr.app.config]."""
    model_id = "test-merge-123"

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

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

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

    # Verify pyproject.toml has merged sections
    job_dir = upload_dir / model_id
    pyproject_doc = parse((job_dir / "pyproject.toml").read_text())

    assert "tool" in pyproject_doc
    assert "flwr" in pyproject_doc["tool"]
    assert "app" in pyproject_doc["tool"]["flwr"]
    assert "config" in pyproject_doc["tool"]["flwr"]["app"]

    config_section = pyproject_doc["tool"]["flwr"]["app"]["config"]
    assert "models" in config_section
    assert "my_model" in config_section["models"]
    assert config_section["models"]["my_model"]["path"] == "unet"
    assert "metrics" in config_section

    # Verify config.toml only has FLIP parameters
    config_doc = parse((job_dir / "config.toml").read_text())
    assert config_doc["flip-model-id"] == model_id
    assert config_doc["flip-project-id"] == "project-123"
    assert config_doc["flip-cohort-query"] == "*"
    assert "models" not in config_doc
    assert "metrics" not in config_doc


def test_upload_app_routes_checkpoints_to_checkpoints_dir(
    client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get
):
    """Test that .pt and .pth files are routed to checkpoints_dir, not upload_dir."""
    model_id = "test-checkpoint-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    checkpoint_content = b"fake model weights"

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/model.pt": checkpoint_content,
        f"https://example.com/{model_id}/other_model.pth": checkpoint_content,
    })

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/model.pt",
            f"https://example.com/{model_id}/other_model.pth",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    # Checkpoints should be in checkpoints_dir
    model_checkpoints = checkpoints_dir / model_id
    assert (model_checkpoints / "model.pt").exists()
    assert (model_checkpoints / "other_model.pth").exists()

    # Checkpoints should NOT be in upload_dir
    job_dir = upload_dir / model_id
    assert not (job_dir / "model.pt").exists()
    assert not (job_dir / "other_model.pth").exists()

    # pyproject.toml should be in upload_dir
    assert (job_dir / "pyproject.toml").exists()


def test_upload_app_moves_app_config_to_root(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
    """Test that app/config.toml is moved to root level config.toml."""
    model_id = "test-config-move-123"

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

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

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

    # config.toml should exist at root
    assert (job_dir / "config.toml").exists()

    # app/config.toml should be gone (moved)
    assert not (job_dir / "app" / "config.toml").exists()


def test_upload_app_evaluation_requires_models_section(
    client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get
):
    """Test that evaluation apps require a [models] section in config.toml."""
    model_id = "test-eval-validation-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 1
"""

    config_json_content = b"""
{
    "job_type": "evaluation"
}
"""

    # Config WITHOUT models section
    config_content = b"""
[metrics]
accuracy = true
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/config.json": config_json_content,
        f"https://example.com/{model_id}/app/config.toml": config_content,
    })

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/config.json",
            f"https://example.com/{model_id}/app/config.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    # Should fail validation
    assert response.status_code == 500
    assert "models" in response.json()["detail"].lower()


def test_upload_app_evaluation_success_with_models(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
    """Test that evaluation apps succeed when they have a [models] section."""
    model_id = "test-eval-success-123"

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 1
"""

    config_json_content = b"""
{
    "job_type": "evaluation"
}
"""

    config_content = b"""
[models.my_model]
path = "unet"
checkpoint = "model.pt"
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
        f"https://example.com/{model_id}/config.json": config_json_content,
        f"https://example.com/{model_id}/app/config.toml": config_content,
    })

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[
            f"https://example.com/{model_id}/pyproject.toml",
            f"https://example.com/{model_id}/config.json",
            f"https://example.com/{model_id}/app/config.toml",
        ],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200
    result = response.json()
    assert result["job_type"] == "evaluation"


def test_upload_app_download_failure(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
    """Test that upload fails gracefully when file download fails."""
    model_id = "test-download-fail-123"

    # Mock only one URL, making the second fail
    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": b"content",
    })

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

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


def test_upload_app_cleans_existing_directories(client, upload_dir, checkpoints_dir, monkeypatch, mock_requests_get):
    """Test that existing job directories are cleaned before upload."""
    model_id = "test-clean-123"

    # Create existing directories with old files
    job_dir = upload_dir / model_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "old_file.txt").write_text("old content")

    model_checkpoints = checkpoints_dir / model_id
    model_checkpoints.mkdir(parents=True, exist_ok=True)
    (model_checkpoints / "old_model.pt").write_text("old model")

    pyproject_content = b"""
[tool.flwr.app]
publisher = "test"

[tool.flwr.app.config]
num_server_rounds = 3
"""

    mock_requests_get({
        f"https://example.com/{model_id}/pyproject.toml": pyproject_content,
    })

    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("MODEL_CHECKPOINTS_DIR", str(checkpoints_dir))

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

    # Old files should be gone
    assert not (job_dir / "old_file.txt").exists()
    assert not (model_checkpoints / "old_model.pt").exists()

    # New file should exist
    assert (job_dir / "pyproject.toml").exists()
