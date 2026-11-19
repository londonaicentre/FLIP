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

import importlib

from fl_api import app as app_module
from fl_api.schemas import HealthResponse


def _ok_schema(spec: dict, path: str, method: str) -> dict:
    """The JSON schema of a route's 200 response."""
    return spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]


def test_docs_and_openapi_contract(client):
    docs_response = client.get("/docs")
    openapi_response = client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert "Swagger UI" in docs_response.text
    assert openapi_response.status_code == 200

    spec = openapi_response.json()
    for path in (
        "/health",
        "/register_node",
        "/check_server_status",
        "/check_client_status",
        "/list_runs",
        "/submit_run/{job_folder}",
        "/submit_tutorial/{tutorial_name}",
        "/abort_run/{run_id}",
        "/upload_app/{model_id}",
    ):
        assert path in spec["paths"]

    assert _ok_schema(spec, "/health", "get")["$ref"] == "#/components/schemas/HealthResponse"
    assert _ok_schema(spec, "/check_server_status", "get")["$ref"] == "#/components/schemas/ServerInfoModel"
    assert _ok_schema(spec, "/check_client_status", "get")["items"]["$ref"] == "#/components/schemas/ClientInfoModel"
    assert _ok_schema(spec, "/list_runs", "get")["items"]["$ref"] == "#/components/schemas/JobMetadata"
    assert _ok_schema(spec, "/submit_run/{job_folder}", "post")["type"] == "string"
    assert _ok_schema(spec, "/abort_run/{run_id}", "delete")["$ref"] == "#/components/schemas/JobMetadata"


def test_health_success(client):
    response = client.get("/health")

    assert response.status_code == 200
    HealthResponse.model_validate(response.json())


def test_health_names_the_build(client, monkeypatch):
    """/health reports the baked FLIP_RELEASE, so a running FL API can say which build it is."""
    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    assert client.get("/health").json()["version"] == "v9.9.9"


def test_openapi_names_the_same_build(monkeypatch):
    """FastAPI reads the version at app construction, so reload the app module under the release."""
    monkeypatch.setenv("FLIP_RELEASE", "v9.9.9")
    try:
        importlib.reload(app_module)
        assert app_module.app.version == "v9.9.9"
    finally:
        monkeypatch.undo()
        importlib.reload(app_module)
