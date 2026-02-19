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
        "/check_server_status",
        "/check_client_status",
        "/list_runs",
        "/submit_run",
        "/abort_run",
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
    submit_schema = spec["paths"]["/submit_run"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    abort_schema = spec["paths"]["/abort_run"]["delete"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert health_schema["$ref"] == "#/components/schemas/HealthResponse"
    assert server_status_schema["$ref"] == "#/components/schemas/ServerInfoModel"
    assert client_status_schema["items"]["$ref"] == "#/components/schemas/ClientInfoModel"
    assert list_schema["items"]["$ref"] == "#/components/schemas/RunRecord"
    assert submit_schema["$ref"] == "#/components/schemas/FlowerCommandResponse"
    assert abort_schema["$ref"] == "#/components/schemas/FlowerCommandResponse"


def test_health_success(client):
    response = client.get("/health")

    assert response.status_code == 200
    HealthResponse.model_validate(response.json())
