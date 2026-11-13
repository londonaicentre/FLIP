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

# FLARE FL API

Base FastAPI service for the FLARE deployment runtime. Its image, `flare-fl-api`, runs as one FL API
container per net (`fl-api-net-N`).

The **Central Hub** drives a net through this API: it submits and monitors jobs and checks the
status of the FL server and clients. Underneath, the API calls the NVIDIA FLARE admin `Session`
through a thin subclass, `FLIP_Session`
([`fl_api/utils/flip_session.py`](./fl_api/utils/flip_session.py)), which wraps a few `Session`
calls with little change in behaviour.

## Ports

The API listens on `8000` inside the container. The standalone dev harness,
[`../compose.dev.yml`](../compose.dev.yml), publishes it on the host as `FL_API_PORT`; in a full
deployment it is reached over the Docker network rather than a host port.

## Authentication

Every route except the health one requires the hub's FL API key: the caller sends `FL_API_KEY` in the
`FL_API_KEY_HEADER` header (default `X-FL-API-Key`) and the API compares it in constant time
([`fl_api/utils/auth.py`](./fl_api/utils/auth.py), byte-identical in both backends). Missing or wrong key
is a 401; an API started without `FL_API_KEY` answers 401 on every protected route and says so in its boot
log. The key is dedicated to this hop — flip-api holds it (`make up` mints it into the hub env file;
on ECS it is a `secrets` entry of every task that calls the API) and it opens nothing on the hub.

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
| `/abort_job/{job_id}` | DELETE | Abort a job, running or still waiting to be scheduled (FLARE marks a waiting one `FINISHED:ABORTED`). |
| `/check_server_status` | GET | FLARE server status. |
| `/check_client_status` | GET | Per-client status; `?targets=<name>` (repeatable) to filter, otherwise all clients. |

All of these wrap `FLIP_Session` except `upload_app`, which only stages files and config.

`/health/` is for container health checks and is not called by the Hub. Besides liveness it reports
`version`, the build the container runs: the baked `FLIP_RELEASE` image tag, or the pyproject
version for a local build.

### Debug / admin endpoints

The routers also expose FLARE admin operations for manual debugging and direct interaction with a
net — `get_system_info`, `get_connected_client_list`, `get_working_directory`, `restart`,
`shutdown`, `shutdown_system`, `show_errors`, `show_stats`, `reset_errors`, `delete_job`,
`download_job`, `get_available_apps_to_upload`. These are **not** part of the Central Hub contract;
see `/docs` for their signatures.

Training code in a running job does not use this API: it reaches the Central Hub directly through
the `flip` package ([`flip-utils/flip/`](../../../flip-utils/flip/)).

## Bundle-fetch origin allow-list (`BUNDLE_URL_ALLOWED_ORIGINS`)

`/upload_app/{model_id}` fetches every `bundle_urls` entry **server-side**, so an unchecked URL is
a blind SSRF from anything holding the FL API key (or reaching the API should the key leak).
`validate_bundle_url` in
[`fl_api/utils/validation.py`](./fl_api/utils/validation.py) guards the fetch in layers:

1. **`BUNDLE_URL_ALLOWED_ORIGINS` — the primary control.** A comma-separated list of the only
   origins a bundle may be fetched from, each a bare `scheme://host[:port]` matched exactly as a
   (scheme, host, port) triple (host case-insensitive, trailing root label ignored, a missing port
   is the scheme's default; no suffix or wildcard form — a suffix would admit any bucket in the
   region). A malformed entry fails the boot. It is checked *before* any name is resolved, so an
   off-list origin never causes a DNS query — the resolver query itself is the out-of-band signal
   of a blind SSRF — and a listed origin is operator-declared, so it is not resolved either (the
   dev compose store resolves to a private docker address by design). **Every hub deployment sets
   it from the object store the hub presigns against, not by hand:** on AWS, flip-api pins
   `AWS_ENDPOINT_URL_S3=https://s3.<AWS_REGION>.amazonaws.com`, which makes boto3 presign
   *path-style* URLs — bucket in the path, host exactly `s3.<AWS_REGION>.amazonaws.com` — so
   `https://s3.<AWS_REGION>.amazonaws.com` is the value, set in
   `deploy/compose.prod.<backend>.yml` and, on ECS, in `deploy/providers/AWS/locals.tf` from
   the same local as flip-api's endpoint (a static test in `deploy/providers/AWS/tests/` holds both
   fl-api task families to it); the dev compose lists its RustFS service, `http://object-store:9000`
   (FLIP#1291). Moving the presign endpoint without the allow-list 400s every bundle download, i.e.
   every training run.
2. **Without a list: https on 443 plus resolve-and-recheck — defence in depth.** Always: no
   private / loopback / link-local / reserved IP literal in any spelling (FLIP#893) and no
   `localhost`, listed or not. With the list empty the URL must also be https on the default port,
   and a DNS name is resolved with every returned address, both families, held to the same range
   check — failing closed on a resolver error (FLIP#905). This is a resolve-then-fetch pair: the
   fetch resolves the name again through `requests`, so a DNS-rebinding attacker answering public
   to the check and private to the fetch wins that race. Pinning the connection to the checked
   address would need a custom transport and is deliberately not built; the allow-list is the
   control that matters. Redirects are refused on the fetch itself.

**Empty is loud, not fatal.** If the variable is unset, the API still starts but logs a `WARNING`
naming it once at startup (and again on the first validation if startup was bypassed). The warning
says what this means: bundles can be fetched from *any* public https host, guarded only by the range
check and its rebinding window.

The standalone harness in [`../compose.dev.yml`](../compose.dev.yml) leaves it unset on purpose: it
has no hub, so nothing presigns bundle URLs for it. On a hub deployment the warning means a wiring
regression.

Bundle-fetch logs and errors show the URL's origin and file name, without credentials, query parameters
or fragments. Download errors name the exception type rather than repeating its URL-bearing message.
The signed URL is preserved for the actual fetch.

## Testing

Provisioning a real net creates FLARE's `local` and `startup` folders for the server, clients and
API. The tests only need `admin/startup/fed_admin.json`:
[`./tests/utils/test_flip_session.py`](./tests/utils/test_flip_session.py) creates it on the fly, and
[`./tests/conftest.py`](./tests/conftest.py) points `FL_ADMIN_DIRECTORY` at a temporary directory so
existing admin directories are left alone.

Run lint, type-check and the test suite (with coverage) from this directory:

```bash
make local_test     # ruff --fix + mypy + pytest
```
