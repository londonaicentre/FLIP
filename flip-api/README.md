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

# flip-api

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Central Hub API CI](https://github.com/londonaicentre/FLIP/actions/workflows/test_flip_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/test_flip_api.yml)
[![flip-api](https://ghcr-badge.egpl.dev/londonaicentre/flip-api/latest_tag?trim=major&label=flip-api)](https://github.com/londonaicentre/FLIP/pkgs/container/flip-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=flip-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **Central Hub API** is the coordination layer for the FLIP platform. It is the primary interface for researchers
and administrators, managing projects, users, federated learning tasks, model retrieval, and cohort queries across
all connected Trust environments.

## Role in the FLIP Platform

The Central Hub API orchestrates the full lifecycle of a federated learning study:

1. **Project management** — create, approve, and track FL projects across participating Trusts
2. **User management** — via AWS Cognito, managing researcher and administrator accounts
3. **Cohort queries** — dispatch OMOP SQL queries to Trust sites and aggregate results
4. **Model coordination** — trigger training jobs, retrieve aggregated models, and store metrics
5. **Audit logging** — record all significant platform events for governance and compliance

It communicates with each Trust's [trust-api](../trust/trust-api/) and stores all state in a PostgreSQL database.

## Deployment

The flip-api is deployed as a Docker container. In the full local stack, it is started via:

```bash
make central-hub   # starts flip-api and the database
```

or as part of the full platform:

```bash
make up
```

Before starting the platform, generate per-trust API keys and the internal service key:

```bash
make generate-trust-api-keys        # from repo root
make generate-internal-service-key  # from repo root (also invoked automatically by `make up`)
```

`generate-trust-api-keys` updates `TRUST_API_KEYS` and `TRUST_API_KEY_HASHES` in `.env.development`. `generate-internal-service-key` writes `INTERNAL_SERVICE_KEY` and `INTERNAL_SERVICE_KEY_HASH` (used for fl-server-to-hub authentication).
See [`.env.development.example`](../.env.development.example) for the expected format.

The API is served on the port defined by `API_PORT` in [`.env.development.example`](../.env.development.example)
(default: `8080`). Interactive API documentation (Swagger UI) is available at:

```
http://localhost:<API_PORT>/api/docs
```

## Configuration

The flip-api is configured via environment variables. In development these are set in
[`.env.development.example`](../.env.development.example). Key variables include:

| Variable | Description |
| --- | --- |
| `DB_HOST` | PostgreSQL host |
| `DB_PORT` | PostgreSQL port |
| `POSTGRES_USER` | PostgreSQL username |
| `POSTGRES_DB` | PostgreSQL database name |
| `AWS_REGION` | AWS region for Cognito and S3 |
| `AWS_COGNITO_USER_POOL_ID` | AWS Cognito User Pool ID |
| `AWS_COGNITO_APP_CLIENT_ID` | AWS Cognito App Client ID |
| `AES_KEY_BASE64` | Base64-encoded AES-256 key used to encrypt trust task payloads and project IDs. Shared between hub (encryption) and trusts (decryption) |
| `TRUST_API_KEY_HASHES` | JSON dict mapping trust names to SHA-256 hashes of their per-trust API keys (e.g. `{"Trust_1": "<hash>"}`) — used by the hub to authenticate incoming trust requests |
| `UPLOADED_MODEL_FILES_BUCKET` | S3 bucket for uploaded model files |
| `UPLOADED_FEDERATED_DATA_BUCKET` | S3 bucket for storing models and artefacts |

See [`.env.development.example`](../.env.development.example) for the full list of required variables.

## Testing

Tests are split into `tests/unit/` (no real backing services) and `tests/integration/` (real Postgres / Cognito / S3 / sibling APIs). See [Where does my test go?](../CONTRIBUTING.md#where-does-my-test-go) in `CONTRIBUTING.md` for the placement rule.

Run unit tests (no Docker / DB / network needed):

```bash
make unit_test
```

Run integration tests against a throwaway Postgres (Docker required, but no compose stack):

```bash
make integration_test
```

Integration tests use [testcontainers-python](https://github.com/testcontainers/testcontainers-python) to start a `postgres:16-alpine` container per test session and tear it down at the end. The schema is created from `SQLModel.metadata` and seeded with permissions / roles / role-permissions in `tests/integration/conftest.py`; per-test tables are truncated between tests. New integration tests just request the `session` (real DB) and/or `client` (FastAPI `TestClient` wired to the same DB) fixtures — no setup boilerplate.

Tests that touch AWS Cognito, S3, or SES are covered by ticket B2 and are currently skip-marked under `tests/integration/` with a reference to the ticket.

Run the full suite (lint + mypy + pytest, requires the dockerised dependencies):

```bash
make test
```

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Contributing & Development Guide](../CONTRIBUTING.md)
