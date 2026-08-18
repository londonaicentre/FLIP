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
from uuid import uuid4

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
        def _get(url, stream=True, timeout=60, allow_redirects=True):
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_response.is_redirect = False
            mock_response.is_permanent_redirect = False
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
    model_id = str(uuid4())

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
    model_id = str(uuid4())

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


@pytest.mark.parametrize(("trusts", "expected"), [(["trust1"], 1), (["trust1", "trust2"], 2)])
def test_upload_app_injects_min_clients_from_trust_count(trusts, expected, client, upload_dir, mock_requests_get):
    """flip-min-clients carries the participating-trust count into the Flower run config.

    The NVFLARE adapter already does this (``config["min_clients"] = len(trusts)`` in
    prepare_config.configure_server). Without the Flower equivalent the strategy inherits
    flwr's ``min_*_nodes=2`` default, so a single-trust run waits for a second node that
    never arrives — silently, until ``start()``'s 3600s timeout.
    """
    model_id = str(uuid4())

    mock_requests_get({f"https://example.com/{model_id}/app/config.toml": b""})

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=trusts,
        bundle_urls=[f"https://example.com/{model_id}/app/config.toml"],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 200

    config_doc = parse((upload_dir / model_id / "app" / "config.toml").read_text())
    assert config_doc["flip-min-clients"] == expected


def test_upload_app_places_checkpoint_in_job_dir(client, upload_dir, mock_requests_get):
    """Checkpoint files are downloaded into the job dir alongside the sources.

    They are no longer routed to a separate volume: the evaluation ServerApp reads
    them from the job dir via the injected flip-job-dir run-config value.
    """
    model_id = str(uuid4())

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
    model_id = str(uuid4())

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
    model_id = str(uuid4())

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
    model_id = str(uuid4())

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


def test_upload_app_rejects_non_uuid_model_id(client, upload_dir):
    """A non-UUID model_id is rejected by FastAPI's UUID path-param validation (422)."""
    body = UploadAppRequest(project_id="p", cohort_query="*", trusts=["t"], bundle_urls=[])

    response = client.post("/upload_app/not-a-uuid", json=body.model_dump())

    assert response.status_code == 422


def test_upload_app_rejects_path_traversal_bundle_url(client, upload_dir):
    """A bundle URL whose path escapes the model dir is rejected by the containment check."""
    model_id = str(uuid4())
    body = UploadAppRequest(
        project_id="p",
        cohort_query="*",
        trusts=["t"],
        bundle_urls=[f"https://example.com/{model_id}/../../etc/passwd"],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 400


def test_upload_app_rejects_non_https_bundle_url(client, upload_dir):
    """Non-https bundle URLs (SSRF vector, e.g. the metadata endpoint) are rejected."""
    model_id = str(uuid4())
    body = UploadAppRequest(
        project_id="p",
        cohort_query="*",
        trusts=["t"],
        bundle_urls=[f"http://169.254.169.254/{model_id}/app/config.toml"],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 400


def test_upload_app_rejects_disallowed_bundle_host(client, upload_dir, monkeypatch):
    """When BUNDLE_URL_ALLOWED_HOSTS is set, off-origin bundle URLs are rejected."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_HOSTS", "objectstore.internal")
    model_id = str(uuid4())
    body = UploadAppRequest(
        project_id="p",
        cohort_query="*",
        trusts=["t"],
        bundle_urls=[f"https://example.com/{model_id}/app/config.toml"],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 400


def test_upload_app_rejects_redirect_response(client, upload_dir, monkeypatch):
    """A 3xx redirect on a bundle fetch is rejected: it would dodge validate_bundle_url (SSRF)."""
    model_id = str(uuid4())

    def _redirecting_get(url, stream=True, timeout=60, allow_redirects=True):
        mock_response = Mock()
        mock_response.is_redirect = True
        mock_response.is_permanent_redirect = False
        mock_response.raise_for_status = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        return mock_response

    import fl_api.utils.upload as upload_module

    monkeypatch.setattr(upload_module.requests, "get", _redirecting_get)

    body = UploadAppRequest(
        project_id="project-123",
        cohort_query="*",
        trusts=["trust1"],
        bundle_urls=[f"https://example.com/{model_id}/pyproject.toml"],
    )

    response = client.post(f"/upload_app/{model_id}", json=body.model_dump())

    assert response.status_code == 400
