# Flower FL API

Standalone FastAPI service for Flower deployment runtime.

## Endpoints

- `GET /health`
- `GET /check_server_status`
- `GET /check_client_status?targets=<name>&targets=<name>`
- `GET /list_runs`
- `POST /submit_run?app_folder=<app>`
- `DELETE /abort_run?run_id=<run_id>`

## API docs

With the FL API running on `localhost:8000`, access:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- ReDoc: `http://localhost:8000/redoc`

The submit endpoint starts a Flower run with:

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

The client status endpoint checks configured SuperNode health services from
`SUPERNODE_HEALTH_ADDRESSES` and returns one item per requested target:
- `{"name": "<target>", "status": "CONNECTED"}` when healthy
- `{"name": "<target>", "status": "DISCONNECTED"}` otherwise

If `targets` are omitted, all configured SuperNode names are checked.

## Health status configuration

Set these environment variables in the FL API container:

- `SUPERLINK_HEALTH_ADDRESS` (example: `superlink:9097`)
- `SUPERNODE_HEALTH_ADDRESSES` (example: `supernode-1=supernode-1:9098,supernode-2=supernode-2:9098`)
- `FLOWER_HEALTHCHECK_TIMEOUT_SECONDS` (default: `1.0`)

For Flower runtime services, start SuperLink and SuperNodes with `--health-server-address`
so the gRPC health service is available.

## Development startup with Docker Compose

Use the existing compose startup method:

```bash
docker compose \
  -f deploy/compose.dev.fl-api.yml \
  --env-file .env.flwr.development up \
  --build \
  --force-recreate
```

The FL API container runs uvicorn with `--reload` and watches `/app/fl_api`. Since
`deploy/compose.dev.fl-api.yml` mounts `../fl_services/fl-api/fl_api:/app/fl_api`,
code changes are applied immediately without restarting the container.

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
