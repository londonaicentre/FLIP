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
make integration_test      # flip-api integration tests (from root)
make tests                 # flip-ui + flip-api tests (from root)
# From a service directory (e.g., flip-api/):
make test                  # ruff + mypy + pytest (unit + integration)
make unit_test             # Unit tests only
make integration_test      # Integration tests only (also available from root)
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
- Test placement: a test goes in `tests/integration/` if and only if it touches a real backing service (Postgres via `session` fixture, real or LocalStack-emulated AWS, a running sibling API, real Orthanc/XNAT/OMOP). If every external dependency is mocked, it's a unit test in `tests/unit/`. FastAPI `TestClient` alone does not make a test "integration". See `CONTRIBUTING.md` ("Where does my test go?") for the canonical rule.
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
- `TRUST_INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for trust-internal service auth, sent by every caller (trust-api, imaging-api, fl-client) on every call to imaging-api or data-access-api. Default `X-Trust-Internal-Service-Key`.
- `TRUST_INTERNAL_SERVICE_KEYS` — JSON dict of per-trust plaintext keys; `trust/Makefile` extracts the per-trust value at deploy time and injects it into every trust-internal container as `TRUST_INTERNAL_SERVICE_KEY`. Each trust uses a distinct key — see the **Trust-internal Service Authentication** section below for the threat model. Distinct from the hub's `INTERNAL_SERVICE_KEY*`: per-trust scope, never sent to or stored on the hub.
- `CENTRAL_HUB_API_URL` — public base URL of flip-api (with `/api`); read by flip-ui and trust-api. In prod this is the CloudFront URL.
- `FLIP_API_INTERNAL_URL` — Central-Hub-internal base URL of flip-api (with `/api`); read **only** by fl-server. Must resolve over the Docker network (e.g. `http://flip-api:8000/api`), never the CloudFront URL — CloudFront strips `X-Internal-Service-Key` at the edge.
- `ENFORCE_MFA` — `true` (the `Settings` default; do **not** set in `.env*` files for stag/prod) gates every authenticated route on TOTP enrolment via the app-layer MFA check in `verify_token`. The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator app. Production compose (`compose.production.yml`) passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so the env var can be overridden from `.env.stag`/`.env.prod` for testing, but falls back to the secure `true` default when unset. Intentionally not in `.env.development.example` or AWS Secrets Manager — the Settings default (`true`) is the canonical secure anchor. The UI mirrors this flag from `/users/me/mfa/status` and skips the enrolment redirect when it's false.

## Deployment Architecture

- **Cloud-Only**: Central Hub (ECS Fargate) + Trust (EC2) on AWS
- **Hybrid**: Central Hub on AWS + Trust on local/on-prem host
- Trusts poll Central Hub over HTTPS (outbound only). No inbound ports on trust hosts.
- SSH access via AWS SSM Session Manager only (no port 22 open).

## CI/CD

GitHub Actions: `test_flip_api.yml`, `test_flip_ui.yml`, `test_trust_*.yml`, `docker_build_*.yml`, `validate_terraform.yml`, `secret-scanning.yml`, `docs.yml`, `pr_acceptance_criteria.yml`. Run locally: `make ci` (uses `act`).

### Docker image builds: manual trigger required for branches

**The `docker_build_*.yml` workflows only auto-publish to GHCR on merges to `develop` and `main`.** Branch pushes do NOT build images. If you pin a branch-named tag in a compose file (e.g. `ghcr.io/londonaicentre/flip-api:my-feature-branch`) for prod testing, you must manually trigger the relevant build workflow first via `workflow_dispatch`:

```bash
gh workflow run docker_build_flip_api.yml --ref <branch-name>
gh workflow run docker_build_flip_ui.yml --ref <branch-name>          # only if UI image is consumed; deploy-ui builds locally
gh workflow run docker_build_trust_trust_api.yml --ref <branch-name>
# ...one per service whose image you've pinned
```

Wait for green completion (`gh run list --workflow=docker_build_flip_api.yml --branch <branch>`) before redeploying. The `flip-ui` is rebuilt locally by `make deploy-ui` and does not consume GHCR; the rest do.

## Pre-commit Hooks

TruffleHog, detect-secrets, large file check (max 1000KB), merge conflict markers, YAML validation, private key detection, env var validation. Install: `pre-commit install`.

## Security Rules

- Never commit secrets/credentials (pre-commit hooks enforce this).
- SSH-over-SSM mandatory (no port 22 exposed).
- Never bypass TLS (`curl -k` prohibited).
- Use `AES_KEY_BASE64` for trust communication encryption.
- AWS Cognito for hub auth, per-trust API keys for trust-to-hub auth.
- Internal service key for fl-server-to-hub auth (separate from trust keys).
- Trust-internal service key for trust-api / imaging-api / fl-client → imaging-api / data-access-api auth (per-trust, never leaves trust env). See **Trust-internal Service Authentication** below.
- FL clients intentionally have no Central Hub credentials.
- Do not hardcode env values in Dockerfiles or compose files.

## Trust-internal Service Authentication

**Threat.** Imaging-api proxies privileged XNAT operations using a service account; data-access-api executes arbitrary SQL against OMOP using a service account. Without caller authentication on these APIs, any container on the trust Docker network — or any operator with SSM port-forward access — can drive XNAT-admin operations and run unrestricted OMOP queries. Both surfaces sit behind no inbound firewall on the trust host (everything is internal to the Docker network) and neither used to validate the caller's identity.

**Mitigation.** Every trust-internal call carries a shared-secret header. The header name comes from `TRUST_INTERNAL_SERVICE_KEY_HEADER` (default `X-Trust-Internal-Service-Key`), the value is the per-trust plaintext key from `TRUST_INTERNAL_SERVICE_KEYS`. Receivers (imaging-api, data-access-api) compare the header against their own copy of the key with `hmac.compare_digest` (constant-time, defeats timing side-channels). Senders are trust-api, imaging-api (when calling data-access-api `/cohort/accession-ids`), and fl-client. The same key is held in plaintext by every trust-internal container — the trust boundary is the trust itself, not individual service-pairs within it. `/health` stays unauthenticated so liveness probes keep working.

**Per-trust scope.** Each trust gets a distinct key. A leak in Trust_1 cannot drive operations on Trust_2's APIs. The hub never sees these keys — they live only in trust-side env (extracted by `trust/Makefile` via `get_json_value` at deploy time, exactly like `TRUST_API_KEYS`). This is deliberately distinct from the hub's `INTERNAL_SERVICE_KEY` (which protects fl-server → flip-api on the Central Hub).

**Generating keys.** `make generate-trust-internal-service-keys` (wrapper for `flip-api/src/flip_api/scripts/generate_trust_internal_service_keys.py`) populates `TRUST_INTERNAL_SERVICE_KEYS` in the env file, preserving any keys that already exist. Add `--force` to rotate.

**Per-service code.** The auth check lives in each receiving service's `utils/internal_auth.py`:
- `trust/imaging-api/imaging_api/utils/internal_auth.py` — applied at the router level on every imaging-api router except `/health`.
- `trust/data-access-api/data_access_api/utils/internal_auth.py` — applied at the router level on `/cohort` (covers `/cohort`, `/cohort/dataframe`, `/cohort/accession-ids`).

The senders construct the header inline at call sites:
- `trust-api/trust_api/services/task_handlers.py::_trust_internal_headers()` — used on outbound imaging-api and data-access-api calls.
- `imaging-api/imaging_api/services_external/data_access.py` — used on the outbound `/cohort/accession-ids` call.
- The [`flip` Python package](https://github.com/londonaicentre/flip-fl-base/tree/main/flip) — lives in `flip-fl-base` and is consumed by both the NVFLARE (`flip-fl-base`) and Flower (`flip-fl-base-flower`) fl-client / fl-server images. Wraps every fl-client call to imaging-api (`flip.get_by_accession_number`, etc.) and data-access-api (`flip.get_dataframe`). The package reads `TRUST_INTERNAL_SERVICE_KEY` from `os.environ` and forwards it on every request. **User-uploaded training code (`client_app.py`, `server_app.py`, anything under `tutorials/`) does not deal with the header directly** — it calls `flip.*` and the package handles transport-level auth. Adding the header to these wrappers is a single follow-up PR in `flip-fl-base`, required before this branch can ship a working trust deployment.

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
