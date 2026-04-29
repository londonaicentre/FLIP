# CLAUDE.md — FLIP

## Project Overview

FLIP (Federated Learning Interoperability Platform) — open-source platform for federated training and evaluation of
medical imaging AI models across healthcare institutions while preserving data privacy.

**License**: Apache 2.0 — all source files must include the copyright header.

## Repository Structure

```
FLIP/
├── flip-api/           # Central Hub API (Python/FastAPI)
├── flip-ui/            # Frontend UI (Vue 3 / TypeScript / TailwindCSS)
├── trust/
│   ├── trust-api/      # Trust API gateway (Python/FastAPI)
│   ├── data-access-api/# OMOP database queries (Python/FastAPI)
│   ├── imaging-api/    # DICOM image retrieval (Python/FastAPI)
│   ├── omop-db/        # Mocked OMOP database (PostgreSQL)
│   ├── orthanc/        # Mocked PACS server
│   └── xnat/           # Mocked XNAT neuroimaging service
├── deploy/             # Docker Compose files (dev/prod, flower/nvflare)
│   └── providers/
│       ├── AWS/        # Terraform/OpenTofu IaC + Ansible for AWS deployment
│       └── local/      # Ansible playbooks for on-premises trust deployment
├── docs/               # Sphinx documentation (ReadTheDocs)
└── scripts/            # Utility scripts
```

Service-specific details are in `flip-api/CLAUDE.md`, `trust/CLAUDE.md`, `trust/*/CLAUDE.md`, and `deploy/providers/AWS/CLAUDE.md`.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend APIs | Python 3.12+, FastAPI, SQLAlchemy/SQLModel, Pydantic |
| Frontend | Vue 3, TypeScript, Vite, TailwindCSS, Pinia |
| Database | PostgreSQL (asyncpg) |
| Package mgmt (Python) | UV (`uv sync`, `uv add`) |
| Package mgmt (JS) | npm |
| Testing | pytest (unit + integration), Vitest (frontend unit), Cypress (frontend e2e) |
| Linters/Formatters | Ruff (Python), MyPy (Python), ESLint (JS/TS) |
| Containers | Docker, Docker Compose, Docker Swarm (XNAT) |
| Infrastructure | Terraform/OpenTofu (AWS), Ansible (EC2 + on-prem provisioning) |
| FL frameworks | NVIDIA FLARE, Flower |
| Auth | AWS Cognito |
| Storage | AWS S3 |
| CI/CD | GitHub Actions |

## Common Commands

### Running Services

```bash
make up                    # Start all services (requires AWS access)
make up-no-trust           # Start central hub only
make up-trusts             # Start trust services only
make down                  # Stop all services
make restart               # Stop and restart all
make build                 # Build all Docker images
make ui                    # Start UI only
make clean                 # Remove all stopped containers, networks, and images
make ci                    # Run CI pipeline locally using act
make central-hub           # Start flip-api + database (no UI)
make debug SERVICE=<name>  # Restart service in debug mode (port 5678)
make debug-off SERVICE=<name>
make debug-all             # Debug all API services
make debug-off-all         # Remove all debug modes
```

### Testing

```bash
make unit_test             # All unit tests across all services (from root)
make tests                 # flip-ui + flip-api tests (from root)
# From a service directory (e.g., flip-api/):
make test                  # ruff + mypy + pytest (unit + integration)
make unit_test             # Unit tests only
make integration_test      # Integration tests only
make local_test            # Tests without Docker
```

### Linting & Type Checking

```bash
uv run ruff check . --fix  # Lint with auto-fix
uv run mypy .              # Static type checking
```

### Debugging

```bash
make debug SERVICE=flip-api        # Start a service in debug mode
make debug SERVICE=trust-api       # Available: flip-api, trust-api, imaging-api, data-access-api
make debug-off SERVICE=flip-api    # Stop debug mode
```

### Test Data

```bash
make -C flip-api create_testing_projects   # Create test projects
make -C flip-api delete_testing_projects   # Clean up test data
```

### Docker Swarm Commands

```bash
docker swarm init                          # Initialize Swarm mode
docker network rm deploy_trust-network-1   # Remove trust network
docker network rm deploy_trust-network-2   # Remove trust network
make create-networks                       # Create all networks
docker compose -f deploy/compose.development.yml exec <service> <command>
docker compose -f deploy/compose.development.yml run --rm <service>
```

### Trust API Key Setup

```bash
make generate-trust-api-keys          # Generate per-trust API keys
make generate-internal-service-key    # Generate fl-server-to-hub key
make -C flip-api generate-trust-key TRUST_NAME=Trust_1  # Single trust key
```

## Workflow Requirements

### Always Use Make Commands

When a Makefile target exists, always use it instead of raw commands. Make targets encapsulate correct flags, environment setup, and command sequences:
- `make test` instead of raw ruff + mypy + pytest
- `make build` instead of raw docker compose build
- `make up`/`make down` instead of raw docker compose

### Always Verify Changes

After code changes, run verification before committing:
1. Identify affected services.
2. Run service-level test: `make test` (or `make unit_test` if no Docker).
3. For cross-service changes, run root-level: `make unit_test`.
4. For frontend changes: `cd flip-ui && npm run lint && npm run test:unit`
5. Fix all failures before committing.

### Documentation Check

After changes, evaluate if docs need updating:

| Change Type | Documentation to Review |
|-------------|------------------------|
| New service/component | `README.md`, `CONTRIBUTING.md`, `docs/source/2_components.rst` |
| New API endpoints | `docs/source/5_api_reference.rst`, service `README.md` |
| Changed env vars | `.env.development.example`, `CONTRIBUTING.md`, `docs/source/3_sys-admin.rst` |
| New dependencies | `CONTRIBUTING.md`, service `README.md` |
| Changed deployment config | `deploy/README.md`, `docs/source/3_sys-admin.rst` |
| New Make targets | `README.md`, this file |
| User-facing workflow changes | `docs/source/4_user-guides.rst` |
| FL framework features | `docs/source/components/component-fl-nodes.rst` |
| Trust service changes | `trust/README.md`, relevant `trust/*/README.md` |
| Auth/role changes | `docs/source/components/component-user-roles.rst` |

## Code Style & Conventions

### Python
- Line length: 120. Linter: Ruff (`select = ['I', 'F', 'E', 'W', 'PT']`). Type checker: mypy.
- Docstrings: Google style. Naming: snake_case. Imports: alphabetically sorted.
- Source layout: `src/[service_name]/`. Tests: `tests/unit/`, `tests/integration/`.
- Dependency injection: FastAPI `Depends()`. Async DB: asyncpg with async context managers.

### JavaScript/TypeScript (flip-ui)
- Line length: 120. Linter: ESLint + TypeScript + Vue plugins.
- Components: PascalCase in `src/partials/` (reusable) and `src/pages/`.
- State: Pinia stores in `src/stores/`. Icons: Heroicons.

### General
- All files include Apache 2.0 copyright header.
- Commits must be signed off (DCO): `git commit -s`
- PRs target `develop`. Branch naming: `[ticket_id]-[task_name]`.

## Environment Setup

1. `cp .env.development.example .env.development`
2. Per service: `cd <service-dir> && uv sync`
3. UI: `cd flip-ui && npm install`
4. AWS: `aws configure sso` (required for flip-api and `make up`)
5. Install AWS Session Manager plugin
6. `make create-networks`

### Key Environment Variables
- `FL_BACKEND` — `flower` (default) or `nvflare`
- `PROD` — `true` (production), `stag` (staging), unset (development)
- `AES_KEY_BASE64` — encryption key for trust communication
- `TRUST_API_KEYS` — JSON dict of per-trust plaintext API keys for trust-to-hub auth
- `TRUST_API_KEY_HASHES` — hub-side JSON dict mapping trust names to SHA-256 hashes of their API keys
- `INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for internal service auth
- `INTERNAL_SERVICE_KEY` — internal service key for fl-server-to-hub auth (Central Hub only)
- `INTERNAL_SERVICE_KEY_HASH` — hub-side SHA-256 hash of the internal service key
- `CENTRAL_HUB_API_URL` — public base URL of flip-api (with `/api`); read by flip-ui and trust-api. In prod this is the CloudFront URL.
- `FLIP_API_INTERNAL_URL` — Central-Hub-internal base URL of flip-api (with `/api`); read **only** by fl-server. Must resolve over the Docker network (e.g. `http://flip-api:8000/api`), never the CloudFront URL — CloudFront strips `X-Internal-Service-Key` at the edge.

## Deployment Architecture

- **Cloud-Only**: Central Hub (ECS Fargate) + Trust (EC2) on AWS
- **Hybrid**: Central Hub on AWS + Trust on local/on-prem host
- Trusts poll Central Hub over HTTPS (outbound only). No inbound ports on trust hosts.
- SSH access via AWS SSM Session Manager only (no port 22 open).

## CI/CD

GitHub Actions: `test_flip_api.yml`, `test_flip_ui.yml`, `test_trust_*.yml`, `docker_build_*.yml`, `validate_terraform.yml`, `secret-scanning.yml`, `docs.yml`, `pr_acceptance_criteria.yml`. Run locally: `make ci` (uses `act`).

## Pre-commit Hooks

TruffleHog, detect-secrets, large file check (max 1000KB), merge conflict markers, YAML validation, private key detection, env var validation. Install: `pre-commit install`.

## Security Rules

- Never commit secrets/credentials (pre-commit hooks enforce this).
- SSH-over-SSM mandatory (no port 22 exposed).
- Never bypass TLS (`curl -k` prohibited).
- Use `AES_KEY_BASE64` for trust communication encryption.
- AWS Cognito for hub auth, per-trust API keys for trust-to-hub auth.
- Internal service key for fl-server-to-hub auth (separate from trust keys).
- FL clients intentionally have no Central Hub credentials.
- Do not hardcode env values in Dockerfiles or compose files.

## Code Modification Rules

1. Follow existing code style and conventions.
2. Add/update tests covering new functionality.
3. Run `make test` or `make unit_test` before committing.
4. Update documentation as needed.
5. Commit with clear messages. All commits signed off by human author alone (`git commit -s`).
6. Add new deps to `pyproject.toml` or `package.json`, document in service README.
7. Use SOLID principles. Aim for high test coverage on critical paths.

## Related Repositories

| Repository | Purpose |
|-----------|---------|
| [FLIP](https://github.com/londonaicentre/FLIP) | Main mono-repo |
| [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) | NVIDIA FLARE base library |
| [flip-fl-base-flower](https://github.com/londonaicentre/flip-fl-base-flower) | Flower base library |

## Documentation Files

Key docs (read on demand):
- Auth/deployment: `docs/source/3_sys-admin.rst`
- Components: `docs/source/2_components.rst`
- API reference: `docs/source/5_api_reference.rst`
- User guides: `docs/source/4_user-guides.rst`
- AWS deployment: `deploy/providers/AWS/README.md`
