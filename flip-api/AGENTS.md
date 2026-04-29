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

# AGENTS.md — flip-api

## Scope

Applies to the Central Hub API under `flip-api/`.

## Service Overview

`flip-api` is a FastAPI backend for user auth, projects, model workflows, trust coordination, FL orchestration, cohort
queries, scheduling, file handling, and site configuration. It uses Cognito, SQLModel, asyncpg, Pydantic, AWS S3, and
background scheduler jobs.

## Key Areas

- `src/flip_api/config.py`: Pydantic settings and environment loading.
- `src/flip_api/auth/`: Cognito JWT verification and auth dependencies.
- `src/flip_api/db/`: database connection, models, and seed data.
- `src/flip_api/domain/`: schemas and repository interfaces.
- `src/flip_api/*_services/`: feature service modules and routers.
- `src/flip_api/private_services/`: trust-to-hub internal endpoints.
- `tests/unit/` and `tests/integration/`: pytest suites.

## Commands

Run from `flip-api/`:

```bash
make test              # Ruff + mypy + pytest unit/integration
make unit_test         # Unit tests and step-function tests with skip flags
make integration_test  # Integration tests only
make local_test        # Local tests without Docker client/db
uv run ruff check .    # Lint only when a narrow raw command is appropriate
uv run mypy .          # Type check only when a narrow raw command is appropriate
uv run pytest <path>   # Focused tests
```

## Conventions

- Prefer FastAPI `Depends()` for request-time dependencies.
- Keep repository-interface patterns in `domain/interfaces/` rather than coupling routers directly to persistence.
- Use async DB helpers from `db/database.py`; do not create ad hoc connections.
- Keep seed scripts idempotent and non-destructive. Missing optional external data should generally log and skip;
  unexpected infrastructure failures should still raise.
- Cognito/boto3 exception details can include request IDs or AWS metadata. Log details server-side and return sanitized
  API errors to clients.
- Add unit tests close to the affected module and broaden to integration tests when DB/client behavior changes.
