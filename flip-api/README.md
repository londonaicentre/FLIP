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

Before starting the platform, generate the internal service key:

```bash
make generate-internal-service-key            # from repo root (also invoked automatically by `make up`)
```

`generate-internal-service-key` writes `INTERNAL_SERVICE_KEY` and `INTERNAL_SERVICE_KEY_HASH` to `.env.development` (used for fl-server-to-hub authentication).

Trusts are registered on the **running hub** with `make register-trusts` (shipped dev roster; from the repo root) or `make register-trust KIT=<CODE>` (one trust). Each inserts a `trust` row with its `api_key_hash`, claims an FL kit slot, and fills that trust's kit file `trust/.env.<CODE>.<env>` carrying `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY` — neither plaintext is stored on the hub. `make up` runs `register-trusts` automatically once the hub is up.
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
| `UPLOADED_MODEL_FILES_BUCKET` | S3 bucket for uploaded model files |
| `UPLOADED_FEDERATED_DATA_BUCKET` | S3 bucket for storing models and artefacts |

See [`.env.development.example`](../.env.development.example) for the full list of required variables.

## Database migrations

The PostgreSQL schema is owned by **Alembic**. At container boot the entrypoint runs
`alembic upgrade head` (after Postgres is reachable, before seeding) — a non-zero
migration exit fails fast and never starts the API. This replaces the old
`SQLModel.metadata.create_all()`, which only ever created *missing* tables and never
applied a schema *diff* to an existing database.

- Migration scripts live in `src/flip_api/db/migrations/` (so they ride the dev `src/`
  bind mount and the prod `COPY src/`); config is in `alembic.ini`.
- `alembic.ini` holds **no** database URL or credentials. `env.py` reuses
  `flip_api.db.database.get_engine()`, so migrations inherit the exact same connection
  string and the prod RDS-Proxy IAM authentication (a per-connection token minted
  by a `do_connect` hook). Tests inject their Testcontainers connection via
  `config.attributes["connection"]` instead.

### Workflow

Any schema-affecting change to `db/models/*.py` (new column, altered type, dropped
table, new model, **or a new value on an existing enum**) **must ship an Alembic
revision**. The integration suite's drift guard (`tests/integration/test_migrations.py`)
fails CI otherwise — `compare_metadata` covers tables/columns/types/FKs/indexes, and a
companion check (`test_no_enum_value_drift`) covers native-enum value changes that
`compare_metadata` is blind to.

```bash
make up                         # bring up flip-db + flip-api at the current head first
make migration MESSAGE="add foo to bar"   # autogenerate a revision from the model diff
# --> REVIEW the generated file in src/flip_api/db/migrations/versions/ (see below)
make migrate                    # apply it (alembic upgrade head)
```

Other targets: `make migration_downgrade` (down one), `make migration_history`,
`make migration_current`.

### Reviewing an autogenerated revision

Always hand-review the generated file — autogenerate has two known blind spots here:

1. **Native PostgreSQL `ENUM` types.** Status/action fields (`JobStatus`, `ModelStatus`,
   `TaskStatus`, …) map to ~12 native PG enum types. `create_table` auto-creates the
   type, but `drop_table` does **not** drop it — so a `downgrade` that removes a table
   with an enum column must add an explicit `op.execute("DROP TYPE IF EXISTS <name>")`,
   or a later re-`upgrade` fails with "type already exists" (see the baseline's
   downgrade for the pattern).
2. **Adding a value to an existing enum.** Autogenerate will **not** emit this (it diffs
   only the column's type name), but `test_no_enum_value_drift` fails CI to remind you to
   hand-write the revision. `ALTER TYPE … ADD VALUE` cannot run inside a transaction —
   wrap it in `with op.get_context().autocommit_block(): op.execute(...)`. (The
   integration harness runs migrations on a plain `connect()`, so `autocommit_block`
   works there too.)

The `queries.queried_trust_ids` column is an `ARRAY(UUID)` (`postgresql.ARRAY(sa.UUID())`)
— check it survives any regeneration. `env.py` renders SQLModel's `AutoString` as plain
`sa.String`, so migrations don't need to `import sqlmodel`.

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

Integration tests use [testcontainers-python](https://github.com/testcontainers/testcontainers-python) to start a `postgres:16-alpine` container per test session and tear it down at the end. The schema is built by running the **Alembic migrations** (`alembic upgrade head`, the same DDL dev/prod apply at boot) and then seeded with permissions / roles / role-permissions in `tests/integration/conftest.py`; per-test tables are truncated between tests. Building from migrations (not `create_all`) means `tests/integration/test_migrations.py` doubles as a **drift guard** — a model change shipped without a matching revision fails the suite. New integration tests just request the `session` (real DB) and/or `client` (FastAPI `TestClient` wired to the same DB) fixtures — no setup boilerplate.

### AWS-touching tests (S3, Cognito, SES)

A session-scoped autouse fixture (`aws_mock` in `tests/integration/conftest.py`) enters [`moto.mock_aws()`](https://github.com/getmoto/moto) for the whole test session, intercepting `boto3.client(...)` calls at the botocore layer. The flip-api production code paths in `src/flip_api/utils/s3_client.py`, `src/flip_api/utils/cognito_helpers.py`, and the inline `boto3.client("sesv2", ...)` constructions in `user_services/access_request.py` and `private_services/imaging_notifications.py` all hit the moto fake with no test-only branches in source.

Per-service helper fixtures bootstrap the state each test needs:

- `s3_buckets` — creates the buckets configured in `Settings` (`UPLOADED_MODEL_FILES_BUCKET`, `SCANNED_MODEL_FILES_BUCKET`, `UPLOADED_FEDERATED_DATA_BUCKET`, `FL_APP_DESTINATION_BUCKET`). The base FL application templates are not an S3 bucket — they are read from the local `FL_APP_BASE_DIR` tree baked into the image (FLIP#724).
- `cognito_user_pool` — creates a moto user pool + app client and rebinds `Settings.AWS_COGNITO_USER_POOL_ID` / `AWS_COGNITO_APP_CLIENT_ID` to point at them, clearing the `_cognito_client` `lru_cache` so the next call rebuilds against the fresh IDs.
- `ses_send_email_recorder` — captures every `sesv2.send_email` call. moto v5 explicitly raises `NotImplementedError` on `send_email` with `Content.Template`, and every flip-api SES caller uses templated content; the recorder wraps the production-code path up to the SDK boundary so the test asserts the boto3 call shape (`FromEmailAddress`, `Destination.ToAddresses`, `TemplateName`, `TemplateData`). It's the closest approximation to a real SES round-trip moto's coverage allows today.

Why moto and not LocalStack: `cognito-idp` and `sesv2` are Pro-only on LocalStack — the free tier rejects `CreateUserPool` / `CreateEmailIdentity` outright. moto covers all three in OSS and runs in-process, so there's no container boot per test session.

`aws_mock` also pins `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` / `AWS_DEFAULT_REGION` to test-only stub values in the environment for the duration of the session, and clobbers any `AWS_PROFILE` from the developer's shell. Real-AWS credentials are never reachable while the fixture is active.

Run the full suite (lint + mypy + pytest, requires the dockerised dependencies):

```bash
make test
```

## Further Reading

- [Full FLIP Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [Contributing & Development Guide](../CONTRIBUTING.md)
