<!--
    Copyright (c) 2026 Flower Labs GmbH
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Flower FL API

Standalone FastAPI service for Flower deployment runtime.

## Endpoints

- `GET /health`
- `POST /register_node`
- `GET /check_server_status`
- `GET /check_client_status?targets=<name>&targets=<name>`
- `GET /list_runs`
- `POST /upload_app/{model_id}`
- `POST /submit_run/{job_folder}` — submit a previously uploaded application; `job_folder` is the Central Hub `model_id` (UUID). flip-api's production path (also exposed as the hidden `/submit_job` alias)
- `POST /submit_tutorial/{tutorial_name}` — submit a pre-baked tutorial folder by name (e.g. `xray_classification`); the local tutorial harness targets this
- `DELETE /abort_run/{run_id}`

## API docs

The FL API's host port is not published by default; the standalone dev stack keeps it internal and jobs are submitted via `make submit` (exec into the container). To reach the Swagger UI/OpenAPI JSON/ReDoc from the host, publish port 8000 by editing `fl-services/flower/compose.dev.yml` and re-running `make -C fl-services/flower up`, then access on `localhost:8000`:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- ReDoc: `http://localhost:8000/redoc`

Both submit endpoints (`/submit_run/{job_folder}` for uploaded applications, `/submit_tutorial/{tutorial_name}`
for pre-baked tutorials) start a Flower run with:

```bash
uvx flwr run . local --format json
```

It parses the JSON output from Flower and returns the full payload from the API response.

The list endpoint runs:

```bash
uvx flwr list local --format json
```

It returns a list of dictionaries with:

- all fields from all Flower run objects in the `runs` array.

The abort endpoint runs:

```bash
uvx flwr stop <run_id> local --format json
```

It returns the full JSON payload from Flower.

The server status endpoint checks the Flower SuperLink health service configured by
`SUPERLINK_HEALTH_ADDRESS` and returns:

- `{"status": "RUNNING"}` when gRPC health returns `SERVING`
- `{"status": "STOPPED"}` otherwise

The register node endpoint accepts a JSON body `{"name": "<trust_name>", "node_id": "<flower_node_id>"}`
and stores the mapping in memory. SuperNodes call this at startup so that `check_client_status`
can resolve Flower node IDs to human-readable trust names.

The client status endpoint queries the SuperLink Control API via
`flwr federation list --federation @none/default local --format json` and uses the
registered node mappings to return one item per trust:

- `{"name": "<target>", "status": "CONNECTED"}` when the node is online
- `{"name": "<target>", "status": "DISCONNECTED"}` otherwise

If `targets` are omitted, all registered trust names are returned.

## Health status configuration

Set these environment variables in the FL API container:

- `SUPERLINK_HEALTH_ADDRESS` (example: `superlink:9097`) — for server status checks

## Development startup with Docker Compose

Use the standalone Flower dev stack under `fl-services/flower/`:

```bash
make -C fl-services/flower build   # build the flower-* :dev images (first time / after Dockerfile changes)
make -C fl-services/flower up      # start SuperLink + 2 SuperNodes + fl-api on the :dev images
```

The FL API container runs uvicorn with `--reload` and `fl-services/flower/compose.dev.yml`
mounts `../../fl-services/flower/fl-api-flower/fl_api:/app/fl_api`, so code changes are
applied immediately without restarting the container.

## Lint and Tests

```bash
uv run pytest --tb=short
```

```bash
uv run ruff check . --fix
```

```bash
uv run mypy . --ignore-missing-imports 
```
