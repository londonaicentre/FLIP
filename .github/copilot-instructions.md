# FLIP — GitHub Copilot Instructions

## Project Summary

Federated Learning Interoperability Platform. Monorepo with FastAPI backend, Vue 3 frontend, PostgreSQL, AWS ECS/EC2 infra, and Terraform IaC.

## Build & Test

### Python (all services: flip-api, trust-api, imaging-api, data-access-api)
- Package manager: `uv` (not pip). Install deps: `cd <service> && uv sync`
- Run tests: `cd <service> && make unit_test` (no Docker) or `make test` (full)
- Lint: `uv run ruff check . --fix`
- Type check: `uv run mypy .`
- Ruff config: line-length 120, select I/F/E/W/PT rules

### Frontend (flip-ui)
- `cd flip-ui && npm install`
- `cd flip-ui && npm run test:unit` (Vitest)
- `cd flip-ui && npm run lint` (ESLint)

### Root
- `make unit_test` — all services
- `make tests` — flip-ui + flip-api
- `make up` — start all services locally

### Pre-commit hooks
Install: `pre-commit install`. Hooks: TruffleHog (secrets), detect-secrets, large files (>1MB), merge conflicts, YAML validation, private key detection.

## Architecture

- **flip-api/** — Central Hub, FastAPI + SQLModel/SQLAlchemy + asyncpg. All user data. Key modules: `user_services/`, `project_services/`, `model_services/`, `fl_services/`, `cohort_services/`, `trusts_services/`, `private_services/`
- **flip-ui/** — Vue 3 + TypeScript + TailwindCSS + Pinia. Components in `src/partials/` (reusable) and `src/pages/`, state in `src/stores/`
- **trust/trust-api/** — Trust gateway, polls Central Hub outbound. No inbound ports.
- **trust/imaging-api/** — DICOM retrieval from PACS (Orthanc/XNAT)
- **trust/data-access-api/** — OMOP CDM SQL queries
- **deploy/providers/AWS/** — Terraform/OpenTofu IaC, Ansible provisioning

Pattern: FastAPI `Depends()` for DI, repository pattern in `domain/interfaces/`, asyncpg via async context managers, pytest + factory_boy for test data.

## Code Conventions

- Python: snake_case, Google-style docstrings, type hints required (mypy strict)
- JS/TS: PascalCase components, camelCase variables/functions
- All files need Apache 2.0 copyright header
- Commits: conventional commits (`feat|fix|docs|refactor|test|chore|ci(scope): message`), must be signed-off (`git commit -s`)
- Imports: alphabetically sorted (enforced by ruff I rule)
- Source layout: `src/[service_name]/`, tests in `tests/unit/` and `tests/integration/`
- Line length: 120

## Key Environment Files

- `.env.development` — local dev (copy from `.env.development.example`)
- `.env.stag` — staging
- `.env.production` — production
- `AWS_PROFILE` — `stag` (staging), `prod` (production)
- `FL_BACKEND` — `flower` or `nvflare`

## Important Rules

- Never hardcode secrets. Use env vars. In production: AWS Secrets Manager.
- Never bypass TLS (`curl -k` prohibited). SSH-over-SSM only (no port 22 open).
- All trust communication is outbound (trusts poll hub, hub never connects inbound).
- Before committing: run `make unit_test` in the affected service. Fix all failures.
- After code changes: check if docs need updating (refer to docs/CLAUDE.md for mapping).
- DCO required: `git commit -s`. No co-author references.
- When running bash commands, always pipe large output through `head -100`, `tail -50`, or `grep` to avoid flooding context. Append `| head -100` to commands like docker ps, aws ecs describe-*, aws logs tail, git diff, git log, pytest, or make output unless full output is specifically needed.
