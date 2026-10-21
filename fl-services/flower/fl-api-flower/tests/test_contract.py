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

    health_schema = spec["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    server_status_schema = spec["paths"]["/check_server_status"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    client_status_schema = spec["paths"]["/check_client_status"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    list_schema = spec["paths"]["/list_runs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    submit_schema = spec["paths"]["/submit_run/{job_folder}"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    abort_schema = spec["paths"]["/abort_run/{run_id}"]["delete"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert health_schema["$ref"] == "#/components/schemas/HealthResponse"
    assert server_status_schema["$ref"] == "#/components/schemas/ServerInfoModel"
    assert client_status_schema["items"]["$ref"] == "#/components/schemas/ClientInfoModel"
    assert list_schema["items"]["$ref"] == "#/components/schemas/JobMetadata"
    assert submit_schema["type"] == "string"
    assert abort_schema["$ref"] == "#/components/schemas/JobMetadata"


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
