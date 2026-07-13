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
├── flip-utils/         # FLIP Python library (pip-installable flip-utils)
├── fl-services/        # FL Docker services + network provisioning, per backend (Makefile owns build/provision/up/down/submit; flower also up-secure): fl-services/nvflare/{fl-base,fl-server,fl-client,fl-api-base, provision/{net-*_project_*.yml, scripts/, workspace-{dev,stag,prod}/ gitignored}}, fl-services/flower/{fl-base,superlink,supernode,fl-api-flower, provision/{scripts/, creds/ gitignored}} (#622)
├── fl-apps/            # FL app templates per backend: fl-apps/nvflare/{standard,fed_opt,evaluation,diffusion_model}, fl-apps/flower/{standard,evaluation} + check_required_files.sh (cross-backend CI validator at root)
├── fl-tutorials/       # FL tutorials per backend: fl-tutorials/nvflare/{image_*,testing}, fl-tutorials/flower/{xray_classification,3d_spleen_segmentation*,numpy} (root Makefile forwards by FL_BACKEND); xray classification, spleen seg/eval, diffusion
├── trust/
│   ├── trust-api/      # Trust API gateway (Python/FastAPI)
│   ├── data-access-api/# OMOP database queries (Python/FastAPI)
│   ├── imaging-api/    # DICOM image retrieval (Python/FastAPI)
│   ├── omop-db/        # Mocked OMOP database (PostgreSQL)
│   ├── orthanc/        # Mocked PACS server
│   └── xnat/           # Mocked XNAT neuroimaging service
├── deploy/             # Docker Compose files (dev/prod, flower/nvflare); FL network provisioning now lives under fl-services/<backend>/, not here
│   └── providers/
│       ├── AWS/        # Terraform/OpenTofu IaC + Ansible for AWS deployment
│       ├── kubernetes/ # Helm chart for Kubernetes trust deployment
│       └── local/      # Ansible playbooks for on-premises trust deployment
├── docs/               # Sphinx documentation (ReadTheDocs)
└── scripts/            # Utility scripts (incl. check-fl-provisioned.sh — the `make up` FL-kit guard)
```

Service-specific details are in `flip-api/CLAUDE.md`, `trust/CLAUDE.md`, `trust/*/CLAUDE.md`, and `deploy/providers/AWS/CLAUDE.md`.

The `flip-utils/`, `fl-services/`, `fl-apps/`, and `fl-tutorials/` trees hold the FL base library, Docker services,
app templates, and tutorials for both NVFLARE and Flower. Both
backends are also provisioned in-tree (gitignored): `deploy/fl_backend.mk` points `FL_PROVISIONED_DIR` per-backend at
`fl-services/nvflare/provision/workspace-dev` (nvflare) or `fl-services/flower/provision/creds` (flower) — see
[`README.md#federated-learning-setup`](README.md#federated-learning-setup).

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
make up                    # Start all services (requires AWS access) — pulls images from GHCR
make up BUILD=true         # Same, but rebuild repo-built services from local source instead of pulling
make up-no-trust           # Start central hub only
make up-trusts             # Start trust services only
make down                  # Stop all services
make restart               # Stop and restart all
make build                 # Build all Docker images (standalone, --no-cache; does not start)
make lock                  # Regenerate every uv.lock from its pyproject.toml
make ui                    # Start UI only
make clean                 # Remove all stopped containers, networks, and images
make ci                    # Run CI pipeline locally using act
make central-hub           # Start flip-api + database (no UI)
make debug SERVICE=<name>  # Restart service in debug mode (port 5678)
make debug-off SERVICE=<name>
make debug-all             # Debug all API services
make debug-off-all         # Remove all debug modes
```

#### Dev image sourcing: pull-by-default

In development (`PROD` unset), `make up` **pulls** the repo-built services
(`flip-api`, `trust-api`, `imaging-api`, `data-access-api`, `orthanc`) from GHCR
rather than building them — they carry `image:` + `pull_policy: always` in the dev
compose, with local `src/` bind-mounted on top so live reload still runs your
working copy. Pass `BUILD=true` (e.g. `make up BUILD=true`) to rebuild from source
instead — required after a dependency (`uv.lock`/`pyproject.toml`) or Dockerfile
change, since those live in the image layer, not the mounted `src/`.

- **Prerequisite:** be logged into GHCR (`docker login ghcr.io`) and have the
  `${DOCKER_TAG}` tag published (dev defaults to `:stag`, the `develop` build).
  A failed pull no longer silently falls back to a build.
- **`flip-ui` is the exception** — it has no published GHCR image, so it always
  builds locally. `make build` remains the standalone `--no-cache` builder.
- Stag/prod are unchanged: `PROD=stag|true` selects the prod compose (baked
  `image:`-only services, no mounts).

### Testing

```bash
make unit_test             # All unit tests across all services (from root)
make integration_test      # flip-api + trust integration tests (from root)
make tests                 # flip-ui unit + e2e tests, then flip-api test suite (from root)
make e2e_smoke             # End-to-end smoke against a running stack (see below)
# From a service directory (e.g., flip-api/):
make test                  # ruff + mypy + pytest (unit + integration)
make unit_test             # Unit tests only
make integration_test      # Integration tests only (also available from root and trust/)
make local_test            # Tests without Docker
```

### End-to-End Smoke Test

`make e2e_smoke` (from root) drives a full project lifecycle against an **already-running stack**: create project → submit cohort query → wait for image pull → run FL training → download results. It is the scripted form of the manual UI sanity-check and is **not run in CI**. Long-running (image pull + FL training) — run it in the background.

Prerequisites:
- Stack up via `make up` (central hub + trusts + XNAT) with trusts registered; Orthanc PACS seeded with DICOM data so image pull has something to pull.
- The tutorial files are in-tree at `fl-tutorials/<backend>/` (NVFLARE and Flower both migrated from the legacy fl-base repos).

Defaults track `FL_BACKEND` (default `nvflare`): `MODEL_FILES_DIR` and `QUERY_FILE` point at `fl-tutorials/nvflare/image_classification/xray_classification/`. For Flower, they point at the in-tree `fl-tutorials/flower/xray_classification/`. Common overrides:

```bash
make e2e_smoke FL_BACKEND=flower                               # use the Flower tutorial
make e2e_smoke MODEL_FILES_DIR=/path/app QUERY_FILE=/path/q.sql
make e2e_smoke EXTRA_ARGS="--abort-midway"                     # exercise the FL stop-training path
make e2e_smoke EXTRA_ARGS="--image-pull-threshold 0.5 --image-pull-timeout 1200"
make e2e_smoke EXTRA_ARGS="--project-id <UUID>"               # reuse an approved project (see below)
make e2e_smoke EXTRA_ARGS="--trusts GSTT"                     # subset of trusts (codes/names, comma-separated);
                                                              # a registered-but-offline trust no longer blocks the run
```

The `--project-id <UUID>` override (printed as `project_id=<UUID>` at the start of any run) reuses an
existing approved project: it skips cohort submission + approval and jumps straight to model
create → upload → train → download. The image-pull wait still runs but returns immediately when the
studies are already pulled — so it lets you iterate on training/app code (and re-run after an upload
or fl-api change) without re-creating the project and re-pulling DICOM (~6 min/backend) each time.

**Testing a change on BOTH FL backends in one sitting (the backend switch).** The pulled DICOM lives in
each trust's Orthanc/XNAT, which `make restart-fl` leaves untouched — so you can pull once on the first
backend and *reuse the same project* for the second, skipping the second image pull entirely:

```bash
# 0. Rebuild the fl-api image(s) from your branch first — the running stack serves PUBLISHED images,
#    not your code. Full: `make build-fl FL_BACKEND=<backend>`; fl-api-only fast path:
#    DOCKER_GID=$(id -g) docker compose -f fl-services/<backend>/compose.dev.yml build <fl-api|flip-fl-api>
make e2e_smoke FL_BACKEND=flower                                          # note the printed project_id=<UUID>
make restart-fl FL_BACKEND=nvflare DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev  # switch backend onto rebuilt :dev images
make e2e_smoke FL_BACKEND=nvflare EXTRA_ARGS="--project-id <UUID>"        # reuse project → skip cohort + re-pull
```

Before trusting either run, confirm the live container actually carries your code (the stack silently
runs old images otherwise): `docker exec flip-fl-api-net-1 cat fl_api/utils/upload.py`. See
[Running FL Tutorials Locally](#running-fl-tutorials-locally) for `make build-fl` / `:dev` image details.

### Running FL Tutorials Locally

The NVFLARE tutorials live in `fl-tutorials/` and run on the local NVFLARE simulator (needs a GPU +
the `flare-fl-base` image). Each tutorial carries a `.env.app` and delegates to the shared harness in
`fl-tutorials/nvflare/testing/`. From the repo root:

```bash
make -C fl-tutorials list-tutorials
make -C fl-tutorials download-xray-data                  # xray dataset (HF); spleen: download-spleen-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
make -C fl-tutorials run-all-tutorials                   # all four (heavy; stops on first failure)
make -C fl-tutorials test-template TEMPLATE=fed_opt      # smoke-test a template that has no tutorial
```

The simulator GPU id defaults to `0`; override with `SIM_GPU` in `fl-tutorials/nvflare/testing/.env.testing`.
To iterate on the FL images, `make build-fl` builds them locally as `:dev` (see `fl-services/nvflare/README.md`);
run the stack on them with `make up DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev`.

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

### Database migrations (flip-api)

flip-api's PostgreSQL schema is owned by **Alembic** (`flip-api/src/flip_api/db/migrations/`), not `SQLModel.metadata.create_all`. The flip-api entrypoint runs `alembic upgrade head` before seeding at boot (fail-fast). Any schema-affecting change to `flip-api/src/flip_api/db/models/*.py` **must** ship a revision in the same PR — the integration drift guard (`flip-api/tests/integration/test_migrations.py`) fails otherwise.

```bash
make -C flip-api migration MESSAGE="..."   # autogenerate a revision (flip-db must be up); then review it
make -C flip-api migrate                   # alembic upgrade head
make -C flip-api migration_current         # show current revision
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

### Trust Registration & Key Setup

```bash
make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."  # Scaffold trust/.env.<CODE>.<env>
make register-trusts                  # Register the shipped dev roster (trust/.env.*.development.example)
make register-trust KIT=<CODE>        # Register one trust + fill its kit (creds + hub-shared block)
make sync-trust-kit KIT=<CODE> PROD=<env>  # Rotation only: refresh hub-shared values in trust/.env.<CODE>.<env>
make sync-trust-kits                  # Refresh every locally-present kit file
make generate-internal-service-key    # Generate fl-server-to-hub key
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
| New service/component | `README.md`, `CONTRIBUTING.md`, `docs/source/components.rst` |
| New API endpoints | `docs/source/api-reference.rst`, service `README.md` |
| Changed env vars | `.env.development.example`, `CONTRIBUTING.md`, `docs/source/sys-admin.rst` |
| New dependencies | `CONTRIBUTING.md`, service `README.md` |
| Changed deployment config | `deploy/README.md`, `docs/source/sys-admin.rst` |
| New Make targets | `README.md`, this file |
| User-facing workflow changes | `docs/source/user-guides.rst` |
| FL framework features | `docs/source/components/component-fl-nodes.rst` |
| Trust service changes | `trust/README.md`, relevant `trust/*/README.md` |
| Auth/role changes | `docs/source/sys-admin/admin-user-roles.rst` |

## Code Style & Conventions

### Python

- Line length: 120. Linter: Ruff (`select = ['I', 'F', 'E', 'W', 'PT', 'UP006', 'UP007', 'UP035', 'UP042', 'UP045']`; `UP042` enforces `StrEnum` over the legacy `(str, Enum)` pattern). Type checker: mypy.
- Docstrings: Google style. Naming: snake_case. Imports: alphabetically sorted.
- Source layout: `src/[service_name]/`. Tests: `tests/unit/`, `tests/integration/`.
- Test placement: a test goes in `tests/integration/` if and only if it touches a real backing service (Postgres via `session` fixture, real AWS, a running sibling API, real Orthanc/XNAT/OMOP). If every external dependency is mocked, it's a unit test in `tests/unit/`. FastAPI `TestClient` alone does not make a test "integration". See `CONTRIBUTING.md` ("Where does my test go?") for the canonical rule.
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

- `FL_BACKEND` — `nvflare` (default) or `flower`. Two roles: (1) deploy layer — selects which fl-* images run (compose/Makefile); (2) flip-api — sets the `FLNets.fl_backend` column at seed time. The seeded value is **canonical**: flip-api reads `FL_BACKEND` only at seeding and never reconciles it at runtime. To switch frameworks, `make restart-fl FL_BACKEND=...` recreates flip-api so its startup seeding re-applies the backend onto every net.
- `FL_PROVISIONED_DIR` — path to the in-tree provisioned FL artifacts, derived per-backend by `deploy/fl_backend.mk` from `FL_BACKEND`: `fl-services/nvflare/provision/workspace-dev` (nvflare startup kits) or `fl-services/flower/provision/creds` (flower per-net TLS certs + SuperNode keys). Both gitignored. Read only by the dev compose overlays for the cert/workspace volume mounts; override at the CLI for a one-off (`make up FL_PROVISIONED_DIR=...`). FL Makefiles are **per-backend** — each `fl-services/<backend>/Makefile` owns that backend's `build`/`provision`/`up`/`down`/`submit` (flower also `up-secure`); the root Makefile forwards only `build-fl` by `FL_BACKEND`. Each backend's `fl-services/<backend>/Makefile` also owns its network provisioning (NVFLARE adds `provision`/`provision-2-nets`/`provision-stag`/`provision-prod`/`upload-kits-to-s3`; the project YAMLs, `scripts/`, and gitignored `workspace-{dev,stag,prod}/` output live under `provision/`). Provision with `make -C fl-services/nvflare provision-2-nets` (nvflare) or `make -C fl-services/flower provision NET_NUMBER=<N>` (flower). To run a backend standalone + submit without the full stack: `make -C fl-services/<backend> up` (or `up-secure`) then `make -C fl-services/<backend> submit APP=<job>`.
- `FL_APP_BASE_DIR` — Local directory holding the base FL application templates (the repo's `fl-apps/` tree), baked into the flip-api image and bind-mounted in dev. flip-api walks `<FL_APP_BASE_DIR>/<backend>/<job_type>/` to bundle an application (uploading those files into `FL_APP_DESTINATION_BUCKET/<model_id>`) and reads each backend's manifest from `<FL_APP_BASE_DIR>/<backend>/required_files.json`. Default `/app/fl-apps`; override to mount operator-provided templates. Replaces the removed `FL_APP_BASE_BUCKET` S3 dependency (FLIP#724): base templates are no longer published to S3 (the `fl-apps-push-s3-*` sync workflows are gone), so a template hotfix now ships by rebuilding + redeploying the flip-api image rather than syncing S3. `fl-apps/` is baked into the image via a BuildKit named build context (`fl_apps=../fl-apps`) since it sits outside flip-api's build context.
- `FL_KIT_SLOT_NAMES` — JSON list (e.g. `["Trust_1", "Trust_2"]`) of FL kit-slot names for the hub's `fl_kit_slot` pool that `register_trust` claims from; each name must match a provisioned participant kit (in-tree workspace for dev, `s3://<AICENTRE_BUCKET_NAME>/fl-flare-participant-kits/<FLARE_KIT_DATE>/net-<N>/services/<slot>/` for stag/prod; slot names are global across nets — every net carries a kit per name). The pool is seeded at flip-api boot and **reconciled on demand** when a registration finds it exhausted (`resolve_fl_kit_slot_names`, additive — never deletes or re-assigns rows); only then does `NoFreeKitSlotError` surface. Single source per env: dev = this env var (a `DevSettings`-only field; restart to change, settings load once); stag/prod = the `fl_kit_slot_names` key of the FLIP_API Secrets Manager secret (Terraform-rendered from this var; deliberately **no env fallback** — a broken/missing key means the pool can't grow, loudly, and is never masked by stale task-def env). Growing the pool is an env-file edit + `make -C deploy/providers/AWS apply-flip-secret` — **no restart, no task-definition change** (caution: that target re-renders every key in the secret from the env file). One-command workflow: `make -C deploy/providers/AWS add-fl-kits N=<n> PROD=stag|true` (mint on every net → additive S3 upload → env edit → secret apply); full runbook in `fl-services/nvflare/README.md` ("Onboarding a new client onto an existing network"). NVFLARE-only dynamics — Flower's SuperNode key labelling reads the list at net startup.
- `PROD` — `true` (production), `stag` (staging), unset (development)
- `AES_KEY_BASE64` — encryption key for trust communication
- A remote trust operator only needs their kit file (`trust/.env.<KIT>`) — no hub `.env.<env>` needed on trust hosts.
  See `trust/README.md` for the standalone-operator quick-start.
- `TRUST_API_KEY` — single per-trust plaintext API key, lives only in that trust's kit file (`trust/.env.<CODE>.<env>`), never on the hub
- `INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for internal service auth
- `INTERNAL_SERVICE_KEY` — internal service key for fl-server-to-hub auth (Central Hub only)
- `INTERNAL_SERVICE_KEY_HASH` — hub-side SHA-256 hash of the internal service key
- `TRUST_INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for trust-internal service auth, sent by every caller (trust-api, imaging-api, fl-client) on every call to imaging-api or data-access-api. Default `X-Trust-Internal-Service-Key`.
- `TRUST_INTERNAL_SERVICE_KEY` — per-trust plaintext key carried in the trust's kit file (`trust/.env.<CODE>.<env>`), minted by `register_trust`. Read by every trust-internal container; used by trust-api / imaging-api / data-access-api / fl-client to authenticate one another inside the trust. Each trust uses a distinct key — see the **Trust-internal Service Authentication** section below for the threat model. Distinct from the hub's `INTERNAL_SERVICE_KEY*`: per-trust scope, never sent to or stored on the hub.
- Trusts are NOT enumerated in the hub env file. The kit files (`trust/.env.<CODE>.<env>`) ARE the roster: `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."` scaffolds one, `make register-trust KIT=<CODE>` registers it. The old `TRUST_<n>_NAME` / `TRUST_<n>_CODE` / `TRUST_<n>_REGION` / `TRUST_<n>_HOST` deploy vars and `register-trust-<n>` targets are removed.
- `CENTRAL_HUB_API_URL` — public base URL of flip-api (with `/api`); read by flip-ui and trust-api. In prod this is the CloudFront URL.
- `FLIP_API_INTERNAL_URL` — Central-Hub-internal base URL of flip-api (with `/api`); read **only** by fl-server. Must resolve over the Docker network (e.g. `http://flip-api:8000/api`), never the CloudFront URL — CloudFront strips `X-Internal-Service-Key` at the edge.
- Database auth — In production (`ENV=production`) flip-api authenticates to Postgres through **RDS Proxy** using a short-lived AWS IAM auth token minted per-connection by a SQLAlchemy `do_connect` hook in `flip_api/db/database.py` (passwordless engine URL, `sslmode=require`, `pool_pre_ping` + `pool_recycle`). The proxy reaches RDS with the rotating RDS-managed master secret it re-reads natively, so the app holds no static DB credential and secret rotation no longer causes an outage (FLIP#556). Dev (`ENV=development`) uses the static `POSTGRES_PASSWORD` from the environment. There is no separate toggle — the path is selected by `ENV`.
- `ENFORCE_MFA` — `true` (the `Settings` default; do **not** set in `.env*` files for stag/prod) gates every authenticated route on TOTP enrolment via the app-layer MFA check in `verify_token`. The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator app. Production compose (`compose.production.yml`) passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so the env var can be overridden from `.env.stag`/`.env.prod` for testing, but falls back to the secure `true` default when unset. Intentionally not in `.env.development.example` or AWS Secrets Manager — the Settings default (`true`) is the canonical secure anchor. The UI mirrors this flag from `/users/me/mfa/status` and skips the enrolment redirect when it's false.
- `MAX_MODEL_FILE_BYTES` — Hard cap on the file size of a single model-file upload, in bytes. Bound on the presigned POST policy so S3 rejects oversized payloads at the edge — the hub never sees them. Default `5368709120` (5 GiB) — raised from 100 MiB so large evaluation checkpoints (e.g. the ~759 MiB Ark+ weights, ~1.5 GiB for the multimodel variant) can be uploaded; such checkpoints are staged server-side by the FL API and loaded by the fl-server, not bundled into the app deployed to clients. The S3 policy condition allows a small fixed overhead above this for multipart/form-data framing (see `_MULTIPART_OVERHEAD_BUFFER_BYTES` in `flip_api/utils/s3_client.py`); the UI guard at `flip-ui/src/utils/file.ts` compares against this raw value so a file at exactly the cap is accepted on both sides.
- `PRE_SIGNED_URL_EXPIRATION_SECONDS` — Setting default for the model-file presigned POST policy TTL, in seconds. The hub silently clamps to the 1800s (30 min) security ceiling encoded as `MAX_PUT_PRESIGNED_URL_TTL_SECONDS` in `flip_api/utils/s3_client.py` (a leaked policy is a writable capability against the upload bucket, so the leak window must stay tight). The ceiling is 1800s rather than the original 600s to give larger uploads (up to the `MAX_MODEL_FILE_BYTES` cap) enough time to complete. Setting default is `3600`; effective ceiling 1800. Over-ceiling callers leave a warning in the logs.

## Deployment Architecture

- **Cloud-Only**: Central Hub (ECS Fargate) + Trust (EC2) on AWS
- **Hybrid**: Central Hub on AWS + Trust on local/on-prem host
- Trusts poll Central Hub over HTTPS (outbound only). No inbound ports on trust hosts.
- SSH access via AWS SSM Session Manager only (no port 22 open).

## CI/CD

GitHub Actions: `test_flip_api.yml`, `test_flip_ui.yml`, `test_trust_*.yml`, `docker_build_*.yml`, `validate_terraform.yml`, `secret-scanning.yml`, `docs.yml`, `pr_acceptance_criteria.yml`. Run locally: `make ci` (uses `act`).

### Docker image builds: gated on tests, manual trigger for branches

**The application `docker_build_*.yml` workflows (`flip_api`, `trust_trust_api`, `trust_imaging_api`, `trust_data_access_api`) auto-publish to GHCR only after their service's test workflow passes on `develop` or `main`.** They trigger via `workflow_run` on the matching test workflow (`FLIP API CI`, `Trust - Trust API CI`, etc.) and a job-level `if` gates on `workflow_run.conclusion == 'success'` — a red test suite never publishes. Path filtering is inherited from the test workflow, so a build still only fires when that service changed. (`orthanc`, `xnat_*` keep their direct push trigger — they have no test suite to gate on; `flip-ui` is a CI smoke test that never publishes.)

Every publish also pushes an immutable **`sha-<short7>`** tag (first 7 chars of the built commit) alongside the mutable `:stag`/`:prod` tags. Hub ECS deploys pin these sha tags via task-definition revisions — `make deploy-centralhub` resolves the env branch tip's tag, `make rollback-centralhub` repoints at the previous revision (FLIP#751; see `deploy/providers/AWS/README.md` "Central Hub deploys and rollback").

> **Note:** `workflow_run` triggers only take effect once these workflow files are on the repo's **default branch**. The first merge that introduces them won't retroactively publish; subsequent qualifying pushes will.

Branch pushes do NOT build images. If you pin a branch-named tag in a compose file (e.g. `ghcr.io/londonaicentre/flip-api:my-feature-branch`) for prod testing, manually trigger the relevant build workflow via `workflow_dispatch` (which bypasses the test gate):

```bash
gh workflow run docker_build_flip_api.yml --ref <branch-name>
gh workflow run docker_build_flip_ui.yml --ref <branch-name>          # only if UI image is consumed; deploy-ui builds locally
gh workflow run docker_build_trust_trust_api.yml --ref <branch-name>
# ...one per service whose image you've pinned
```

Wait for green completion (`gh run list --workflow=docker_build_flip_api.yml --branch <branch>`) before redeploying. The `flip-ui` is rebuilt locally by `make deploy-ui` and does not consume GHCR; the rest do.

## Pre-commit Hooks

TruffleHog, detect-secrets, large file check (max 1000KB), merge conflict markers, YAML validation, private key detection, env var validation, fl-apps required-files generation (`fl-apps-required-files` — regenerates each `fl-apps/<backend>/required_files.json` from its per-template arrays via `fl-apps/check_required_files.sh`; rewrites-and-fails on drift like `prettier`, so re-stage and commit again — backstopped by the `check-required-files` CI workflow. The aggregate is `linguist-generated` in `.gitattributes`; never hand-edit it — edit the per-template `required_files.json`), uv lockfile sync (`uv-lock`, one entry per uv project). Install: `pre-commit install`.

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
- 72-hour supply-chain cooldown on Python/npm package installs — enforced by uv `exclude-newer` (`[tool.uv]` in every `pyproject.toml`) and npm `min-release-age` (`flip-ui/.npmrc`, requires npm >= 11.10 which Node 24 LTS ships), backstopped by a `uv lock --check` CI gate in `secret-scanning.yml`. See CONTRIBUTING.md ("Dependency cooldown").

## Trust-internal Service Authentication

**Threat.** Imaging-api proxies privileged XNAT operations using a service account; data-access-api executes arbitrary SQL against OMOP using a service account. Without caller authentication on these APIs, any container on the trust Docker network — or any operator with SSM port-forward access — can drive XNAT-admin operations and run unrestricted OMOP queries. Both surfaces sit behind no inbound firewall on the trust host (everything is internal to the Docker network) and neither used to validate the caller's identity.

**Mitigation.** Every trust-internal call carries a shared-secret header. The header name comes from `TRUST_INTERNAL_SERVICE_KEY_HEADER` (default `X-Trust-Internal-Service-Key`), the value is the per-trust `TRUST_INTERNAL_SERVICE_KEY` from the trust's kit file (`trust/.env.<CODE>.<env>`). Receivers (imaging-api, data-access-api) compare the header against their own copy of the key with `hmac.compare_digest` (constant-time, defeats timing side-channels). Senders are trust-api, imaging-api (when calling data-access-api `/cohort/accession-ids`), and fl-client. The same key is held in plaintext by every trust-internal container — the trust boundary is the trust itself, not individual service-pairs within it. `/health` stays unauthenticated so liveness probes keep working.

**Per-trust scope.** Each trust gets a distinct key. A leak in Trust_1 cannot drive operations on Trust_2's APIs. The hub never sees these keys — they live only in trust-side env: `register_trust` writes `TRUST_INTERNAL_SERVICE_KEY` into the trust's kit file (`trust/.env.<CODE>.<env>`), which `trust/Makefile` `-include`s so every trust-internal container inherits it. This is deliberately distinct from the hub's `INTERNAL_SERVICE_KEY` (which protects fl-server → flip-api on the Central Hub).

**Generating keys.** The key is minted by `register_trust` (`make register-trusts`), which writes `TRUST_INTERNAL_SERVICE_KEY` into the trust's kit file. Re-register to rotate.

**Per-service code.** The auth check lives in each receiving service's `utils/internal_auth.py`:

- `trust/imaging-api/imaging_api/utils/internal_auth.py` — applied at the router level on every imaging-api router except `/health`.
- `trust/data-access-api/data_access_api/utils/internal_auth.py` — applied at the router level on `/cohort` (covers `/cohort`, `/cohort/dataframe`, `/cohort/accession-ids`).

The senders construct the header inline at call sites:

- `trust-api/trust_api/services/task_handlers.py::_trust_internal_headers()` — used on outbound imaging-api and data-access-api calls.
- `imaging-api/imaging_api/services_external/data_access.py` — used on the outbound `/cohort/accession-ids` call.
- The `flip` Python package — lives at [`flip-utils/flip/`](flip-utils/flip/) in this mono-repo, consumed by both the NVFLARE and Flower fl-client / fl-server images built from `fl-services/`. Wraps every fl-client call to imaging-api (`flip.get_by_accession_number`, etc.) and data-access-api (`flip.get_dataframe`). The package reads `TRUST_INTERNAL_SERVICE_KEY` from `os.environ` and forwards it on every request. **User-uploaded training code (`client_app.py`, `server_app.py`, anything under `tutorials/`) does not deal with the header directly** — it calls `flip.*` and the package handles transport-level auth.

## Code Modification Rules

1. Follow existing code style and conventions.
2. Add/update tests covering new functionality.
3. Run `make test` or `make unit_test` before committing.
4. Update documentation as needed.
5. Commit with clear messages. All commits signed off by human author alone (`git commit -s`).
6. Add new deps to `pyproject.toml` or `package.json`, document in service README.
7. Use SOLID principles. Aim for high test coverage on critical paths.

## Documentation Files

Key docs (read on demand):

- Auth/deployment: `docs/source/sys-admin.rst`
- Components: `docs/source/components.rst`
- API reference: `docs/source/api-reference.rst`
- User guides: `docs/source/user-guides.rst`
- AWS deployment: `deploy/providers/AWS/README.md`
