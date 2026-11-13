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

FastAPI service that flip-api calls to drive a Flower SuperLink: it uploads and submits apps, lists and aborts runs, and reports server and client status.

## Authentication

Every route except the health one requires the hub's FL API key: the caller sends `FL_API_KEY` in the
`FL_API_KEY_HEADER` header (default `X-FL-API-Key`) and the API compares it in constant time
([`fl_api/utils/auth.py`](./fl_api/utils/auth.py), byte-identical in both backends). Missing or wrong key
is a 401; an API started without `FL_API_KEY` answers 401 on every protected route and says so in its boot
log. The key is dedicated to this hop — flip-api holds it (`make up` mints it into the hub env file;
on ECS it is a `secrets` entry of every task that calls the API) and it opens nothing on the hub.

## Endpoints

- `GET /health` — liveness, and `version`: the baked `FLIP_RELEASE` image tag, or the pyproject version for a local build
- `POST /register_node`
- `GET /check_server_status`
- `GET /check_client_status?targets=<name>&targets=<name>`
- `GET /list_runs`
- `POST /upload_app/{model_id}`
- `POST /submit_run/{job_folder}` — submit a previously uploaded application; `job_folder` is the Central Hub `model_id` (UUID). flip-api's production path (also exposed as the hidden `/submit_job` alias)
- `POST /submit_tutorial/{tutorial_name}` — submit a pre-baked tutorial folder by name (e.g. `xray_classification`); the local tutorial harness targets this. The name is resolved to `/app/src/<category>/<name>` (mirroring `fl-tutorials/flower/<category>/<name>`) and must match exactly one folder; a flat `/app/src/<name>` is never used
- `DELETE /abort_run/{run_id}`

## API docs

The FL API's port is not published to the host. The standalone dev stack keeps it internal, and jobs are submitted with `make submit`, which execs into the container. To open the API docs from the host:

1. Publish port 8000 in `fl-services/flower/compose.dev.yml`.
2. Re-run `make -C fl-services/flower up`.
3. Open one of:
   - Swagger UI: `http://localhost:8000/docs`
   - OpenAPI JSON: `http://localhost:8000/openapi.json`
   - ReDoc: `http://localhost:8000/redoc`

## What each endpoint runs

Both submit endpoints (`/submit_run/{job_folder}` for uploaded applications, `/submit_tutorial/{tutorial_name}`
for pre-baked tutorials) start a Flower run with:

```bash
flwr run . local --format json
```

The API parses Flower's JSON output, answers 500 if Flower reports a failure, and otherwise returns the new run's id.

Both pass `flip-job-dir` (the app directory) in a `--run-config` file. `/submit_run` merges the uploaded
`app/config.toml` into it, which is how a platform run gets its settings. `/submit_tutorial` does not: a tutorial's
`config.toml` holds the settings it is uploaded to the platform with, so a tutorial run uses the tutorial's own
`pyproject.toml` values, as the Flower simulator does (FLIP#1432).

The list endpoint runs:

```bash
flwr list local --format json
```

It returns one `{"job_id", "status"}` item per run in Flower's `runs` array, with Flower's status mapped to
the shared job-status values.

The abort endpoint runs:

```bash
flwr stop <run_id> local --format json
```

It returns `{"job_id", "status"}`: `STOPPED`, or the run's terminal status if it had already finished.

The server status endpoint requests the SuperLink's own `/health` route, on the same listener as
its Control API (`SUPERLINK_ADDRESS`, port 8000), and returns:

- `{"status": "RUNNING"}` when it answers 200
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

## Environment variables

- `SUPERLINK_ADDRESS` (example: `superlink:8000`) — the SuperLink's HTTP listener; `/check_server_status` requests its
  `/health` route, so the check needs no setting of its own
- `SUPERLINK_ROOT_CERTIFICATES` — when set, the check is HTTPS verified against this CA; unset
  (the standalone insecure dev stack), it is plain HTTP
- `FLOWER_HEALTHCHECK_TIMEOUT_SECONDS` (default `1.0`)
- `FL_API_KEY`, `FL_API_KEY_HEADER` — see [Authentication](#authentication)
- `BUNDLE_URL_ALLOWED_ORIGINS` — see below
- `JOB_CREDENTIAL_DIR` — where the job's hub credential is written for the fl-server; see
  [`fl-services/AGENTS.md`](../../AGENTS.md#environment-variables)

Flower 1.43 removed the separate gRPC health server (port 9097) the check used to call (FLIP#1914).

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

**Empty is loud, not fatal.** The standalone harness in [`../compose.dev.yml`](../compose.dev.yml)
has no hub and leaves the variable unset — nothing presigns bundle URLs for it — and the API logs a
`WARNING` naming the variable once at startup (and again on the first validation if startup was
bypassed) saying exactly what is disabled: fetches from *any* public https host, with only the
range check and its rebinding window left. On a hub deployment that warning is a wiring regression.

Bundle-fetch logs and errors show the URL's origin and file name, without credentials, query parameters
or fragments. Download errors name the exception type rather than repeating its URL-bearing message.
The signed URL is preserved for the actual fetch.

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

From `fl-services/flower/fl-api-flower/`:

```bash
uv run pytest --tb=short
uv run ruff check . --fix
uv run mypy . --ignore-missing-imports
```
