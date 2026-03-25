# CLAUDE.md

## Project Overview

FLIP (Federated Learning Interoperability Platform) is an open-source platform for federated training and evaluation of
medical imaging AI models across healthcare institutions while preserving data privacy. Developed by the London AI
Centre with Guy's and St Thomas' NHS Foundation Trust and King's College London.

**License**: Apache 2.0 — all source files must include the copyright header.

## Repository Structure

Three Mono-repo with these key services:

```bash
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

## Tech Stack

| Layer | Technology |
| ------- | ----------- |
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
```

### Testing

```bash
# From root — run all unit tests across all services
make unit_test

# From root — run flip-ui + flip-api tests
make tests

# From a service directory (e.g., flip-api/)
make test                  # Lint + mypy + pytest (unit + integration)
make unit_test             # Unit tests only
make integration_test      # Integration tests only
make local_test            # Tests without Docker

# Direct pytest invocation
uv run pytest --tb=short --disable-warnings --cov=src/ --cov-report=term-missing
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

## Workflow Requirements

### Always Use Make Commands

When a `Makefile` target exists for the task at hand, **always use it** instead of running raw commands. Make targets encapsulate the correct flags, environment setup, and command sequences. Key rules:

- Use `make test` (from a service directory) rather than invoking `uv run ruff`, `uv run mypy`, and `uv run pytest` separately — it runs all three in the correct order.
- Use `make unit_test` (from root or service directory) for running unit tests.
- Use `make build` instead of raw `docker compose build`.
- Use `make up` / `make down` instead of raw `docker compose` commands.
- Check each service's `Makefile` for available targets before writing manual commands.

### Always Verify Changes

After making any code changes, **always run verification before committing**:

1. **Identify affected services** — determine which service(s) your changes touch.
2. **Run the service-level test suite** — from the affected service directory:

   ```bash
   make test        # Runs ruff + mypy + pytest (unit + integration)
   ```

   If only unit tests are needed (e.g., no Docker available):

   ```bash
   make unit_test   # Unit tests only
   ```

3. **For cross-service changes**, run the root-level test suite:

   ```bash
   make unit_test   # All services from root
   ```

4. **Fix all failures** before committing — do not commit code that fails linting, type checking, or tests.
5. **For frontend changes** (`flip-ui/`), also run:

   ```bash
   cd flip-ui && npm run lint && npm run test:unit
   ```

### Check If Documentation Needs Updating

After making changes, **always evaluate whether documentation needs to be updated**. Check the following:

| Change Type | Documentation to Review |
| ------------- | ------------------------ |
| New service or component | `README.md` (root), `CONTRIBUTING.md` (Adding a new service section), `docs/source/2_components.rst` |
| New API endpoints | `docs/source/5_api_reference.rst`, service-level `README.md` |
| Changed environment variables | `.env.development.example`, `CONTRIBUTING.md` (Environment variables section), `docs/source/3_sys-admin.rst` |
| New dependencies or tooling | `CONTRIBUTING.md` (Prerequisites section), service `README.md` |
| Changed Docker/deployment config | `deploy/README.md`, `docs/source/3_sys-admin.rst` |
| New Make targets | `README.md` (root), this file (`CLAUDE.md`) |
| Changed user-facing workflows | `docs/source/4_user-guides.rst` and files in `docs/source/user-guides/` |
| New FL framework features | `docs/source/components/component-fl-nodes.rst` |
| Trust service changes | `trust/README.md`, relevant `trust/*/README.md` |
| New user roles or auth changes | `docs/source/components/component-user-roles.rst` |

Documentation files in this repo:

- **`README.md`** (root) — project overview, quickstart, prerequisites
- **`CONTRIBUTING.md`** — development setup, coding style, PR process
- **`docs/source/`** — Sphinx/ReadTheDocs documentation (`.rst` files)
- **Service-level `README.md`** files — per-service setup and usage (in `flip-api/`, `flip-ui/`, `trust/*/`)
- **`deploy/README.md`** — deployment instructions
- **This file (`CLAUDE.md`)** — AI assistant reference

When in doubt, update the docs. Outdated documentation is worse than no documentation.

## Code Style & Conventions

### Python

- **Line length**: 120 characters
- **Linter/Formatter**: Ruff (`select = ['I', 'F', 'E', 'W', 'PT']`)
- **Type checker**: mypy (all code must have type hints)
- **Docstrings**: Google style guide
- **Naming**: snake_case for functions, variables, modules
- **Imports**: Alphabetically sorted (enforced by ruff `I` rule)
- **Source layout**: `src/[service_name]/` per service
- **Test files**: `test_*.py` or `*_test.py` in `tests/unit/` and `tests/integration/`
- **Test fixtures**: pytest fixtures in `conftest.py`, factory_boy for data generation
- **Dependency injection**: FastAPI `Depends()`
- **Async DB**: asyncpg with async context managers

### JavaScript/TypeScript (flip-ui)

- **Line length**: 120 characters
- **Linter**: ESLint with TypeScript + Vue plugins
- **Components**: PascalCase names, organized in `src/partials/` (reusable) and `src/pages/`
- **State management**: Pinia stores in `src/stores/`
- **Styling**: TailwindCSS utility classes (dark mode via `dark:` prefix)
- **Testing**: Vitest (unit), Cypress (e2e)
- **Icons**: Heroicons (`@heroicons/vue`)

### General

- All files must include the Apache 2.0 copyright header
- Commits must be signed off (DCO): `git commit -s`
- PRs target the `develop` branch
- Branch naming: `[ticket_id]-[task_name]` (e.g., `19-ci-pipeline-setup`)

## Environment Setup

1. Copy env template: `cp .env.development.example .env.development`
2. Install Python deps per service: `cd <service-dir> && uv sync`
3. Install UI deps: `cd flip-ui && npm install`
4. Configure AWS: `aws configure sso` (required for `flip-api` and `make up`)
5. Docker networks: `make create-networks`

### Key Environment Variables

- `FL_BACKEND` — `flower` (default) or `nvflare`
- `PROD` — `true` (production), `stag` (staging), unset (development)
- `AES_KEY_BASE64` — encryption key for trust communication
- `PRIVATE_API_KEY` — service-to-service auth

## CI/CD

GitHub Actions workflows in `.github/workflows/`:

- **Test**: `test_flip_api.yml`, `test_flip_ui.yml`, `test_trust_*.yml`
- **Build**: `docker_build_*.yml` for each service
- **Infrastructure**: `validate_terraform.yml` (fmt + validate, no AWS creds needed)
- **Security**: `secret-scanning.yml` (TruffleHog + detect-secrets)
- **Docs**: `docs.yml` (Sphinx → ReadTheDocs)
- **PR checks**: `pr_acceptance_criteria.yml`

Run CI locally: `make ci` (uses [act](https://github.com/nektos/act))

## Pre-commit Hooks

Configured in `.pre-commit-config.yaml`:

- TruffleHog (secret scanning)
- detect-secrets (with `.secrets.baseline`)
- Large file check (max 1000KB)
- Merge conflict markers, YAML validation, private key detection
- Environment variable validation

Install: `pre-commit install`

## Deployment & Infrastructure

### Deployment Models

FLIP supports two deployment models:

1. **Cloud-Only**: Central Hub + Trust services both on AWS EC2
2. **Hybrid/On-Premises**: Central Hub on AWS EC2 + Trust services on a local/on-premises host

Both models use HTTPS with TLS certificates. Trust communication is encrypted via `AES_KEY_BASE64`.

### Docker Compose (Development vs Production)

Compose files live in `deploy/` and are selected by the `PROD` environment variable and `FL_BACKEND`:

| File | Purpose |
| ------ | --------- |
| `compose.development.yml` | Builds from source, volume-mounts code, exposes debug ports |
| `compose.production.yml` | Pulls pre-built images from GHCR (`ghcr.io/londonaicentre/*`) |
| `compose.{env}.flower.yml` | FL backend override for Flower |
| `compose.{env}.nvflare.yml` | FL backend override for NVIDIA FLARE |
| `compose.development.debug.override.yml` | Debug port mappings (port 5678) |

**Development** builds images locally and mounts source for live editing. **Production** pulls tagged images from GHCR and uses AWS Secrets Manager for credentials instead of `.env` files.

When modifying Docker Compose configuration, **always update both development and production compose files** to keep them consistent. Check that:

- New services appear in both `compose.development.yml` and `compose.production.yml`
- Environment variables, ports, and network configurations match across environments
- New FL-backend-specific services appear in both `flower` and `nvflare` variants

### AWS Infrastructure (Terraform/OpenTofu)

Infrastructure-as-code lives in `deploy/providers/AWS/`. Key resources:

| Resource | Purpose |
| ---------- | --------- |
| VPC + subnets | Network isolation (`10.0.0.0/16`, 2 AZs) |
| EC2 instances | Central Hub (`t3.small`) + Trust (`t3.small`, optional) |
| RDS PostgreSQL | Managed database (private subnets) |
| ALB | HTTPS termination for UI and API (ACM certificate) |
| NLB | gRPC traffic for FL servers |
| S3 buckets | Model files, federated data, FL app storage |
| Cognito | User pool (`flip-user-pool`) with email auth |
| Secrets Manager | `FLIP_API` secret (AES key, DB password, trust endpoints, CA certs) |
| SES | Email notifications |
| Route53 | DNS records for ALB subdomain |

**Terraform state** is stored remotely in S3 with encryption and DynamoDB locking.

### Deployment Commands

```bash
# From deploy/providers/AWS/
make full-deploy PROD=stag           # Full staging deployment
make full-deploy PROD=true           # Full production deployment
make full-deploy-stag-hybrid LOCAL_TRUST_IP=<ip>  # Hybrid with on-prem trust

# Individual steps
make init                            # Terraform init (S3 backend)
make plan                            # Terraform plan
make apply                           # Terraform apply
make deploy-centralhub               # Deploy Central Hub services to EC2
make deploy-trust                    # Deploy Trust services to EC2
make status                          # Health checks across all services

# On-premises trust
make add-local-trust LOCAL_TRUST_IP=<ip>       # Provision on-prem trust via Ansible
make test-local-trust LOCAL_TRUST_IP=<ip>      # Validate trust connectivity (with TLS)

# Certificate management
make gen-trust-ec2-certs             # Generate TLS certs for cloud Trust EC2
make regen-trust-certs               # Regenerate expired certs
```

### Trust Services Architecture

Each Trust environment (cloud or on-prem) runs:

| Service | Port | Purpose |
| --------- | ------ | --------- |
| nginx-tls | 8020 | HTTPS termination |
| trust-api | 8000 | Trust API gateway |
| imaging-api | 8000 | DICOM image retrieval |
| data-access-api | 8000 | OMOP database queries |
| fl-client | 8002-8003 | Federated learning client |
| XNAT | 8104 | Neuroimaging platform |
| Orthanc | 4242 | DICOM server |
| omop-db | 5432 | Patient cohort database |

On-premises trusts are provisioned via Ansible (`deploy/providers/local/`), which installs Docker, generates TLS certificates, configures the firewall, and deploys the Trust Docker Compose stack.

### Dev/Prod Consistency Rules

When making infrastructure or deployment changes, **always think through both environments end-to-end**:

1. **Compose files** — update both `compose.development.yml` and `compose.production.yml`. They differ in *how* services run (build-from-source vs GHCR images, source volume mounts vs baked-in code), but the set of services, ports, networks, and functional volume mounts must stay in sync.
2. **Volume mounts and host files** — development mounts files from the local repo (e.g., `../trust/certs:/etc/ssl/trust/:ro`). Production mounts files from the EC2 host filesystem (e.g., `/opt/flip/certs/trust-ca.crt:/etc/ssl/trust/trust-ca.crt:ro`). If you add a file bind mount in development, the same file must exist on the production EC2 instance — ensure it is provisioned by the Ansible playbook (`deploy/providers/AWS/site.yml`) or by a Makefile target, and add the corresponding mount in `compose.production.yml`.
3. **FL backend variants** — update both `flower` and `nvflare` compose files if adding services or ports.
4. **Environment variables** — add to `.env.development.example` and document in `deploy/README.md`. Production uses AWS Secrets Manager instead of `.env` files, so also update `deploy/providers/AWS/services.tf` (the `FLIP_API` secret) if the variable is needed at runtime.
5. **Terraform variables** — update `variables.tf` with descriptions and defaults; keep `main.tf` and `services.tf` in sync.
6. **Ansible provisioning** — if production EC2 instances need new directories, files, packages, or data, add tasks to `deploy/providers/AWS/site.yml` (cloud) and `deploy/providers/local/site_local_trust.yml` (on-prem). Key host paths: `/opt/flip/` (app root), `/opt/flip/certs/` (TLS certs), `/opt/flip/data/` (FL data), `/opt/flip/services/` (FL participant kits), `/opt/flip/volumes/` (database data).
7. **Trust changes** — update both cloud (`deploy/providers/AWS/`) and on-prem (`deploy/providers/local/`) Ansible playbooks so both deployment models stay consistent.
8. **Certificates** — never bypass TLS validation; fix certificates instead.

## Security Rules

- Never commit secrets or credentials — pre-commit hooks enforce this
- Never bypass TLS certificate validation (`curl -k` is prohibited)
- Use `AES_KEY_BASE64` for encrypted trust communication
- AWS Cognito for hub authentication, private API keys for inter-service auth
- Do not hardcode environment values in Dockerfiles or compose files

## Important: Rules for adding or modifying code

Follow these rules when adding new code or modifying existing code:

1. Follow existing code style and conventions
2. Add or update tests in `tests/` to cover new functionality
3. Run formatters, linters, type checkers, and tests before committing (`make test` or `make unit_test`)
4. Update documentation in `README.md` or `docs/` as needed
5. Commit changes with clear messages referencing related issues or features, NEVER co-sign commits or PRs, all commits
must be signed off by the human author alone (`git commit -s`), as they are the sole responsible for the content and
quality of the code.
6. Ensure that any new dependencies are added to the appropriate `pyproject.toml` or `package.json` files and documented
in the service-level `README.md`.
7. Use the SOLID principles for code organization and design, ensuring that new code is modular, reusable, and maintainable.
8. Measure code coverage and aim for high coverage on new code paths, while also ensuring that critical paths are well-tested.

## Related Repositories

| Repository | Purpose |
| ----------- | --------- |
| [FLIP](https://github.com/londonaicentre/FLIP) | Main mono-repo (this repo) |
| [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) | NVIDIA FLARE FL base library |
| [flip-fl-base-flower](https://github.com/londonaicentre/flip-fl-base-flower) | Flower FL base library |

### flip-fl-base (NVIDIA FLARE)

Repository structure for the NVIDIA FLARE federated learning implementation:

```bash
flip-fl-base/
├── flip/                       # Pip-installable Python package
│   ├── constants/              # Platform constants and configuration
│   ├── core/                   # Core platform logic and utilities
│   ├── nvflare/                # NVFLARE-specific components and handlers
│   └── utils/                  # General utility functions
├── fl_services/                # Docker services for FL network
│   ├── fl-api-base/            # FastAPI service for FL run control and status
│   ├── fl-base/                # Base NVFLARE image with dependencies
│   ├── fl-client/              # NVFLARE client container for participants
│   └── fl-server/              # NVFLARE server container for coordinator
├── deploy/                     # Docker Compose configurations
│   ├── compose.yml             # Development/test compose stack
│   ├── compose.test.yml        # Integration testing stack
│   └── release.sh              # Release script
├── tutorials/                  # Example FL applications
│   ├── standard/               # Standard training examples
│   ├── diffusion_model/        # Diffusion model training example
│   ├── evaluation/             # Evaluation-focused examples
│   └── fed_opt/                # Federated optimization examples
├── tests/                      # Unit and integration tests
├── docs/                       # Documentation and assets
├── pyproject.toml              # Python project configuration (UV)
├── Makefile                    # Build and test automation
├── README.md                   # Project overview and quick start
└── LICENSE.md                  # Apache 2.0 license
```

**Key features:**

- NVFLARE-based federated learning framework
- Reusable NVFLARE components and custom handlers
- Docker services for serverless and traditional FL deployment
- Tutorial applications demonstrating different learning paradigms
- Integration with FLIP Central Hub via FL API

### flip-fl-base-flower (Flower Framework)

Repository structure for the Flower federated learning implementation:

```bash
flip-fl-base-flower/
├── src/                        # Pip-installable Python package
│   └── flwr_flip/              # Core Flower-FLIP integration utilities
├── fl_services/                # Docker services for FL network
│   ├── fl-base/                # Base Flower image with dependencies
│   ├── fl-api-flower/          # FastAPI service for run control and status
│   ├── superlink/              # Flower SuperLink (federation coordinator)
│   └── supernode/              # Flower SuperNode (participant worker)
├── deploy/
│   └── compose.yml             # Docker Compose stack for local runtime
├── tutorials/                  # Example Flower applications
│   ├── numpy/                  # Minimal NumPy-based example
│   └── monai/                  # MONAI-based medical imaging example
├── docs/                       # Documentation and assets
│   └── images/                 # Project logos and diagrams
├── tests/                      # Unit and integration tests
├── pyproject.toml              # Python project configuration (UV)
├── Makefile                    # Build and test automation
├── README.md                   # Project overview and quick start
└── LICENSE.md                  # Apache 2.0 license
```

**Key features:**

- Flower framework-based federated learning
- SuperLink/SuperNode topology for scalable federated networks
- Flower API service for orchestrating experiments
- Tutorial applications with medical imaging focus (MONAI)
- Integration with FLIP Central Hub via FL API

### Structure Comparison

| Aspect | flip-fl-base (NVFLARE) | flip-fl-base-flower (Flower) |
| -------- | ------------------------ | ------------------------------ |
| FL Framework | NVIDIA FLARE | Flower |
| Main Package | `flip/` | `src/flwr_flip/` |
| Services | `fl-api-base`, `fl-client`, `fl-server` | `fl-api-flower`, `superlink`, `supernode` |
| Deployment | Traditional client-server | SuperLink/SuperNode topology |
| Tutorial Focus | Multiple paradigms (standard, diffusion, optimization) | Medical imaging (NumPy, MONAI) |
| Docker Compose | `compose.yml`, `compose.test.yml` | Single `deploy/compose.yml` |
