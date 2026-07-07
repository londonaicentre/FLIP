<!--
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

# NVFLARE FL API

Base FastAPI service for the NVFLARE deployment runtime. It backs the per-net FL API instances
(one `flare-fl-api` container per network).

The FL API wraps the NVIDIA FLARE admin `Session`: we subclass it as `FLIP_Session`
([`fl_api/utils/flip_session.py`](./fl_api/utils/flip_session.py)), which wraps a handful of
`Session` calls with minimal behavioural difference. This API is how the **Central Hub** drives a
net — job submission, job monitoring, and status checks of the federated components (server and
clients).

## Ports

The API listens on `8000` inside the container. The dev compose publishes it via `FL_API_PORT`
(see [`../compose.dev.yml`](../compose.dev.yml)); in a full deployment it is reached over the
Docker network rather than a host port.

## Endpoints

The app is a FastAPI instance mounted at the service root (no prefix), grouped by router tag
(`Health`, `Application`, `Jobs`, `System`). The **authoritative, always-current** list — methods,
parameters, and request/response schemas — is the OpenAPI spec served by the running service:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`

This README only pins the **contract** the Central Hub depends on; for exact shapes read `/docs`
or the routers under [`fl_api/routers/`](./fl_api/routers/).

### What the Central Hub calls

The Hub drives a net **only** through these endpoints
(see `flip_api/fl_services/services/fl_service.py`):

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/upload_app/{model_id}` | POST | Stage a ready-to-launch FLARE app — global/local rounds, bundle URLs, project/cohort, trusts, aggregator, aggregation weights — into the API's upload dir. Writes the `config_fed_{server,client}.json` but does **not** call FLARE. |
| `/submit_job/{job_folder}` | POST | Submit a previously staged app as a FLARE job (`session.submit_job`). |
| `/list_jobs` | GET | List FLARE jobs (the Hub filters this to find the job to abort). |
| `/abort_job/{job_id}` | DELETE | Abort a running job. |
| `/check_server_status` | GET | FLARE server status. |
| `/check_client_status` | GET | Per-client status; `?targets=<name>` (repeatable) to filter, otherwise all clients. |

All of these wrap `FLIP_Session` except `upload_app` (file/config staging only). `/health/` (also
served at `/`) reports liveness for container health checks — it is not called by the Hub.

### Debug / admin endpoints

The routers also expose FLARE admin operations for manual debugging and direct interaction with a
net — `get_system_info`, `get_connected_client_list`, `get_working_directory`, `restart`,
`shutdown`, `shutdown_system`, `show_errors`, `show_stats`, `reset_errors`, `delete_job`,
`download_job`, `get_available_apps_to_upload`. These are **not** part of the Central Hub contract;
see `/docs` for their signatures.

When a job is running, the user's training code reaches the Central Hub through the `flip` package
([`flip-utils/flip/`](../../../flip-utils/flip/)) **directly**, not through this API.

## Testing

The nvflare `local` and `startup` folders are created during the provisioning of each real network
(server, client(s) and API). However, Python tests require `fed_admin.json` to exist within
`admin/startup`, so this file is created dynamically in
[`./tests/utils/test_flip_session.py`](./tests/utils/test_flip_session.py) for testing.
`FL_ADMIN_DIRECTORY` is set to a temporary directory in
[`./tests/conftest.py`](./tests/conftest.py) during testing to avoid conflicts with any existing
admin directories.

Run lint, type-check and the test suite (with coverage) from this directory:

```bash
make local_test     # ruff --fix + mypy + pytest
```
