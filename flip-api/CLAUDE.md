# CLAUDE.md — flip-api (Central Hub API)

## Service Overview

Central Hub REST API. FastAPI + asyncpg + SQLModel. Handles user auth (Cognito), project management, trust coordination, FL run orchestration, cohort queries, file management, and scheduling.

## Key Files

| File | Purpose |
|------|---------|
| `src/flip_api/__init__.py` | FastAPI app factory, middleware, router registration |
| `src/flip_api/config.py` | Pydantic settings, env var loading |
| `src/flip_api/db/database.py` | asyncpg async session, DB connection |
| `src/flip_api/db/models/main_models.py` | SQLModel ORM: Project, Trust, Model, File, etc. |
| `src/flip_api/db/models/user_models.py` | User, Role, Permission models |
| `src/flip_api/db/seed/` | DB seed data: roles, permissions, trusts, FL scheduler, banners |
| `src/flip_api/domain/schemas/` | Pydantic request/response schemas |
| `src/flip_api/domain/interfaces/` | Repository interfaces (Dependency Inversion) |
| `src/flip_api/auth/` | Cognito JWT verification, auth middleware |
| `src/flip_api/scripts/` | Key generation (trust, internal service, env utils) |

## Service Modules

| Module | Purpose |
|--------|---------|
| `user_services/` | Register, authenticate, update/delete users, roles, permissions |
| `project_services/` | Project CRUD, approval workflows |
| `model_services/` | ML model management, metrics, logs, approvals |
| `fl_services/` | FL training initiation, status, stop, file pull |
| `trusts_services/` | Trust registration, health checks, imaging creation |
| `cohort_services/` | Cohort query submission, results retrieval |
| `step_functions_services/` | Step function orchestration (register user, approve, cohort) |
| `file_services/` | S3 file upload/download |
| `private_services/` | Trust-to-hub internal endpoints (tasks, cohort results) |
| `site_services/` | Site configuration, details |
| `role_services/` | Role CRUD |
| `scheduler/` | APScheduler background jobs (FL scheduling, trust polling) |
| `shared/` | Shared utilities, middleware |

## Commands (from `flip-api/`)

```bash
make test          # ruff + mypy + pytest (unit + integration)
make unit_test     # ruff + mypy + pytest unit + step function tests (--skip-client --skip-db)
make integration_test  # Integration tests only
make local_test    # Tests without Docker (--skip-client --skip-db)
make lint          # ruff check --fix (in Docker)
make mypy          # mypy type check (in Docker)
make build         # docker compose build
make up            # Start flip-db then flip-api
make down          # Stop flip-api then flip-db
make debug         # Restart in debug mode (port 5678)
```

## Conventions

- FastAPI `Depends()` for DI. Repository pattern in `domain/interfaces/`.
- asyncpg connections via async context managers from `db/database.py`.
- pytest + factory_boy for test data. Fixtures in `conftest.py`.
- Ruff config: line-length 120, select I/F/E/W/PT/UP* rules.
- All tests in `tests/unit/` and `tests/integration/`.
