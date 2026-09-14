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
├── fl-apps/            # FL app templates per backend: fl-apps/nvflare/{standard,evaluation,diffusion_model,fed_opt} (all Client-API), fl-apps/flower/{standard,evaluation} + check_required_files.sh (cross-backend CI validator at root)
├── fl-tutorials/       # FL tutorials per backend (all NVFLARE ones are Client-API apps): fl-tutorials/nvflare/{image_*,tabular_classification}, fl-tutorials/flower/{xray_classification,3d_spleen_segmentation*,ehr_risk_prediction} (root Makefile forwards by FL_BACKEND); xray classification, spleen seg/eval, diffusion, EHR risk prediction (tabular/OMOP-only, Synthea open data → `make -C trust load-synthea-ehr`). Shared dataset tooling in fl-tutorials/datasets/ (download/derive/enrich, single copy for both backends — the download-*-data + upload-spleen-labels targets), outputs in the shared gitignored fl-tutorials/data/. fl-tutorials/datasets/utils/ holds the OMOP CDM contract shared by the per-dataset generation chains (#1092): schemas, concept mappings, the per-project surrogate-key blocks (omop_ids.py) and the one verification gate (verify_omop_tables.py --project <name>). spleen carries the full chain (`convert-spleen-to-dicom`, `create-spleen-metadata-table`, `build-spleen-omop-tables`, plus the reproducible-path/verification targets); cxr carries the OMOP conversion only (`reproduce-cxr-omop`) because image generation lives in the private londonaicentre/xraycat. Plus fl-tutorials/tests/ — CPU-only pytest over the tutorial transform chains (#871) plus a static `min_clients` wiring guard covering fl-apps/flower too, run by `make -C fl-tutorials test`
├── map-apps/           # MONAI Application Package (MAP) templates for packaging FLIP-trained models for clinical deployment
├── trust/
│   ├── trust-api/      # Trust API gateway (Python/FastAPI)
│   ├── data-access-api/# OMOP database queries (Python/FastAPI)
│   ├── imaging-api/    # DICOM image retrieval (Python/FastAPI)
│   ├── omop-db/        # Mocked OMOP database (PostgreSQL) + omop-db image build source & populate tooling (#834)
│   ├── orthanc/        # Mocked PACS server
│   └── xnat/           # Mocked XNAT neuroimaging service
├── deploy/             # Docker Compose files (dev/prod, flower/nvflare); FL network provisioning now lives under fl-services/<backend>/, not here
│   └── providers/
│       ├── AWS/        # Terraform/OpenTofu IaC + Ansible for AWS deployment
│       ├── kubernetes/ # Helm chart for Kubernetes trust deployment. The chart holds no AWS credentials and never fetches the FL participant kit: stage it onto the node first with `make -C deploy/providers/kubernetes stage-kit KIT_SRC=<kit dir> KUBE_CONTEXT=<ctx>`, then deploy with `flClient.kitHostPath` pointing at it (required whenever flClient.enabled). On the AWS side the EC2 equivalent is `make stage-fl-kit KIT=<CODE>`, which re-stages for the trust's REGISTERED slot after `register-trusts`
│       └── local/      # Ansible playbooks for on-premises trust deployment
├── docs/               # Sphinx documentation (ReadTheDocs)
└── scripts/            # Utility scripts (incl. check-fl-provisioned.sh — the `make up` FL-kit guard)
```

Detail lives with the code it describes. This file keeps what is true across the repo; each tree's own
`CLAUDE.md` carries the rest:

| File | Covers |
|------|--------|
| `flip-api/CLAUDE.md` | Hub service layout, migrations, the e2e smoke harness, the demo recorder, hub env vars (auth, email, model-file scanning) |
| `trust/CLAUDE.md` | Trust stack, kit files, trust data + seeding, XNAT/PACS env vars, trust-internal service auth |
| `trust/*/CLAUDE.md` | Per-service detail (trust-api, imaging-api, data-access-api, omop-db) |
| `deploy/CLAUDE.md` | Compose layer and the multi-instance knobs (`FLIP_INSTANCE`, `MAIN_ENV_FILE`, `DB_PORT`) |
| `deploy/providers/AWS/CLAUDE.md` | Terraform, the CI plan/apply/drift flow, image tags and deploys |
| `fl-services/CLAUDE.md` | FL images, provisioning, `FL_*` env vars |
| `fl-tutorials/CLAUDE.md` | Tutorials and the shared dataset tooling |
| `docs/CLAUDE.md` | Sphinx docs index and build |

The `flip-utils/`, `fl-services/`, `fl-apps/`, and `fl-tutorials/` trees hold the FL base library, Docker services,
app templates, and tutorials for both NVFLARE and Flower. Both
backends are also provisioned in-tree (gitignored): `deploy/fl_backend.mk` points `FL_PROVISIONED_DIR` per-backend at
`fl-services/nvflare/provision/workspace-dev` (nvflare) or `fl-services/flower/provision/creds` (flower) — see
[`fl-services/nvflare/README.md`](fl-services/nvflare/README.md) and
[`fl-services/flower/README.md`](fl-services/flower/README.md).

## Tech Stack

| Layer | Technology |
| ------- | ----------- |
| Backend APIs | Python 3.12–3.13, FastAPI, SQLAlchemy/SQLModel, Pydantic |
| Frontend | Vue 3, TypeScript, Vite, TailwindCSS, Pinia |
| Database | PostgreSQL (psycopg2 + SQLModel sync sessions; RDS Proxy + IAM auth in prod) |
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
make -C fl-tutorials test  # ruff over fl-tutorials/ + the CPU-only transform-chain suite (no GPU/dataset/FL image)
make e2e_smoke             # End-to-end smoke against a running stack (see below)
# From a service directory (e.g., flip-api/):
make test                  # ruff + mypy + pytest (unit + integration)
make unit_test             # Unit tests only
make integration_test      # Integration tests only (also available from root and trust/)
make local_test            # Tests without Docker
```

### End-to-End Smoke Test

`make e2e_smoke` (from root) drives a full project lifecycle against an **already-running stack**:
create project → submit cohort query → wait for image pull → run FL training → download results. It
is the scripted form of the manual UI sanity-check, is **not run in CI**, and is long-running — run
it in the background.

```bash
make e2e_smoke                                  # defaults track FL_BACKEND (default nvflare)
make e2e_smoke FL_BACKEND=flower
make e2e_smoke EXTRA_ARGS="--project-id <UUID>" # reuse an approved project, skip the ~6 min re-pull
make e2e_smoke EXTRA_ARGS="--no-imaging"        # tabular-only project (has_imaging=false, FLIP#1071)
```

The spleen tutorials additionally need a **data-enrichment** step (label upload to XNAT) between the
image pull and training; `make -C flip-api e2e_smoke_spleen` carries it. Full flag reference, the
enrichment contract, the `$`-escaping trap and the both-backends switch:
[`flip-api/CLAUDE.md`](flip-api/CLAUDE.md#end-to-end-smoke-test).

Tabular-only cohorts (`has_imaging=false`, FLIP#1071) skip the imaging stage entirely; the EHR
risk-prediction tutorials are the first such cohort and have their own target,
`make e2e_smoke_ehr [FL_BACKEND=flower]`, which pins `--no-imaging` for you.

### Running FL Tutorials Locally

```bash
make -C fl-tutorials list-tutorials
make -C fl-tutorials download-xray-data                  # xray dataset (HF); spleen: download-spleen-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
make -C fl-tutorials sim-tutorial TUTORIAL=xray_classification FL_BACKEND=flower   # simulator, no containers
make -C fl-tutorials test                                # ruff + the CPU-only transform-chain suite
```

`make run` delegates to `make sim` (NVFLARE simulator, needs a GPU; per-tutorial `make export` builds
the job config without one). `sim-tutorial` means "no containers" on both backends — an alias for
`run-tutorial` on NVFLARE, an in-process `flwr run` on Flower. Mock OMOP data is generated in-tree
per dataset under `fl-tutorials/datasets/`. Tutorial layout, the simulator paths, the dataset
regeneration chains and their verification gate: [`fl-tutorials/CLAUDE.md`](fl-tutorials/CLAUDE.md).
FL image building (`make build-fl`, the `:dev` tag): [`fl-services/CLAUDE.md`](fl-services/CLAUDE.md).

### Linting & Type Checking

```bash
uv run ruff check . --fix  # Lint with auto-fix
uv run mypy .              # Static type checking
make checkov-lint          # Static checkov security lint over deploy/providers/AWS (credential-free; FLIP#1052/#1058)
```

### Debugging

```bash
make debug SERVICE=flip-api        # Start a service in debug mode
make debug SERVICE=trust-api       # Available: flip-api, trust-api, imaging-api, data-access-api
make debug-off SERVICE=flip-api    # Stop debug mode
```

Debug ports (hub `API_DEBUG_PORT`, trust `TRUST/IMAGING/DATA_ACCESS_DEBUG_PORT`) publish on
`127.0.0.1` only, so the debugger must attach from the same host — for a remote dev box, tunnel the
port (SSH/SSM) rather than exposing it on the LAN.

### Test Data

```bash
make -C flip-api create_testing_projects   # Create test projects
make -C flip-api delete_testing_projects   # Clean up test data
make seed-demo-projects                    # Curated radiology catalogue in honest lifecycle states
                                           # (EXTRA_ARGS="--cleanup" removes it again)
make -C trust seed-trusts PROJECTS="spleen_project cxr_project"   # Seed the RUNNING dev trusts with datasets (#1100)
make -C trust seed KIT=GSTT PROJECTS="…"   # one trust; seed-omop / seed-orthanc for one half
```

**Trust data has two paths** — pre-built snapshots mounted by `make up`, and seeding into a
*running* trust — and a data version is a git tag on `aicentreflip/trust-data`, pinned once in
`trust/.data_version`. Both, plus the publishing rule:
[`trust/CLAUDE.md`](trust/CLAUDE.md#trust-data-snapshots-seeding-and-versioning).

### Demo Video Recorder

`make demo-video` records the full end-to-end walkthrough against the **live dev stack** and
assembles one mp4 (local dev tool, not run in CI; Cypress segments plus off-camera platform waits).
Prerequisites, the `DEMO_ARGS` flags and the demo Cognito users:
[`flip-api/CLAUDE.md`](flip-api/CLAUDE.md#demo-video-recorder).

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
| Central Hub AWS resources (`deploy/providers/AWS/*.tf`) | `deploy/providers/AWS/architecture/central_hub.py` — the drawn-node map; `tests/test_architecture_diagram.py` fails CI when a drawn resource disappears or a load-bearing one is added undrawn. Then `make aws-diagram` for the README copies; `docs/source/components/component-central-hub.rst` if the prose changes |
| New Make targets | `CONTRIBUTING.md`, this file |
| User-facing workflow changes | `docs/source/user-guides.rst` |
| FL framework features | `docs/source/components/component-fl-nets.rst` |
| Trust service changes | `trust/README.md`, relevant `trust/*/README.md`, `docs/source/components/component-trust-apis.rst` |
| Auth/role changes | `docs/source/sys-admin/admin-user-roles.rst` |
| Agent instructions (this file's `CLAUDE`/`AGENTS` pair, at any level) | Both members of the pair, in the same PR: every `CLAUDE`-named instructions file has an `AGENTS`-named mirror in the same directory — an exact copy with only the file name substituted (title + cross-references). Edit the `CLAUDE`-named file, then regenerate its twin from it with `sed 's/CLAUDE\.md/AGENTS.md/g'`; never edit the `AGENTS`-named twin directly. |

## Code Style & Conventions

### Python

- Line length: 120. Linter: Ruff (`select = ['I', 'F', 'E', 'W', 'PT', 'UP006', 'UP007', 'UP035', 'UP042', 'UP045']`; `UP042` enforces `StrEnum` over the legacy `(str, Enum)` pattern). Type checker: mypy.
- Docstrings: Google style. Naming: snake_case. Imports: alphabetically sorted.
- Source layout: `src/[service_name]/`. Tests: `tests/unit/`, `tests/integration/`.
- Test placement: a test goes in `tests/integration/` if and only if it touches a real backing service (Postgres via `session` fixture, real AWS, a running sibling API, real Orthanc/XNAT/OMOP). If every external dependency is mocked, it's a unit test in `tests/unit/`. FastAPI `TestClient` alone does not make a test "integration". Tests for the tutorial tree live outside any service, in `fl-tutorials/tests/` — CPU-only, running in flip-utils' env (`flip-utils[full]`), with fixtures synthesised in-process; anything needing real training stays with the GPU simulator harness. See `CONTRIBUTING.md` ("Where does my test go?") for the canonical rule.
- Dependency injection: FastAPI `Depends()`. DB: sync SQLModel `Session` via `get_session()` — the `with Session(...)` block is load-bearing on error paths (FLIP#773). Prod authenticates through RDS Proxy with a per-connection IAM token (SQLAlchemy `do_connect` hook, passwordless engine URL).

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

Cross-cutting keys and URLs live here. The rest are documented where they are consumed:
**FL** (`FL_BACKEND`, `FL_PROVISIONED_DIR`, `FL_APP_BASE_DIR`, `FL_KIT_SLOT_NAMES`) in
[`fl-services/CLAUDE.md`](fl-services/CLAUDE.md#environment-variables) ·
**multi-instance** (`FLIP_INSTANCE`, `MAIN_ENV_FILE`, `DB_PORT`) in
[`deploy/CLAUDE.md`](deploy/CLAUDE.md#multi-instance-environment-variables) ·
**XNAT/PACS** (`XNAT_PORT`, `PACS_*`, `DQR_*`) in
[`trust/CLAUDE.md`](trust/CLAUDE.md#xnat-and-pacs-environment-variables) ·
**hub auth, email and model-file scanning** (`ENFORCE_MFA`, `EMAIL_BACKEND`, `MAX_MODEL_FILE_BYTES`,
`PRE_SIGNED_URL_EXPIRATION_SECONDS`, the two model-file buckets, `ALLOWED_MODEL_FILE_EXTENSIONS`,
`PICKLESCAN_*`, `BANDIT_TIMEOUT_SECONDS`, `SCHEDULER_MALWARE_SCAN_RECONCILE_RATE`, DB auth) in
[`flip-api/CLAUDE.md`](flip-api/CLAUDE.md#hub-environment-variables).

- `PROD` — `true` (production), `stag` (staging), unset (development)
- `AES_KEY_BASE64` — the platform-wide key for the hub↔trust payload envelope: AES-256-GCM since FLIP#1179 (base64 of `{"v":1,"kid":"shared","iv","ct"}`; version, kid and a caller-supplied *context* — `task:<task_type>`, `project_id`, `xnat_password` — bound into the tag, so every `encrypt`/`decrypt` call site passes the same `context=` and a payload sealed for one purpose does not open for another), with **no compatibility for the pre-#1179 CBC format**, so a hub and every trust registered to it upgrade across that change together (Deployment Mode → quiesce → redeploy hub + trusts). Must be byte-identical on the hub and every trust container that decrypts (trust-api, imaging-api, data-access-api) and decode to exactly 32 bytes — every `get_aes_key()` refuses a 16- or 24-byte key rather than silently running AES-128/192; a mismatch fails closed as `Invalid payload: failed authentication` on every task (imaging-api / data-access-api answer the FL client with a 400). On stag/prod the hub's copy is what the CI Terraform apply wrote into Secrets Manager from the GitHub environment — reconcile the operator env file from deployed state (`deploy/providers/AWS/scripts/reconcile_ci_env.py`), never the other way round. Per-trust keys are the FLIP#845 follow-up.
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

## Deployment Architecture

- **Cloud-Only**: Central Hub (ECS Fargate) + Trust (EC2) on AWS
- **Hybrid**: Central Hub on AWS + Trust on local/on-prem host
- Trusts poll Central Hub over HTTPS (outbound only). No inbound ports on trust hosts.
- SSH access via AWS SSM Session Manager only (no port 22 open).

## CI/CD

GitHub Actions: `test_flip_api.yml`, `test_flip_ui.yml`, `test_trust_*.yml`, `fl-tutorials-tests.yml`, `test_map_apps.yml`, `docker_build_*.yml`, `validate_terraform.yml` (fmt/validate + a checkov security lint over `deploy/providers/AWS/**` — IAM policy content plus promoted posture checks; static, credential-free; local run `make checkov-lint` **from the repo root** (the AWS Makefile's parse-time env guard blocks the `-C` form for contributors), deliberate breadth/posture suppressed in-code with `# checkov:skip=<ID>:<rationale>` — FLIP#1052, FLIP#1058; plus an `AWS deploy tests` job running the credential-free pytest suite in `deploy/providers/AWS/tests/` over the stack's static artefacts — rendered templates, deploy scripts, and Terraform source itself, including the Cognito `callback_urls` = browser CORS allowlist invariants), `terraform_plan.yml`, `terraform_apply.yml`, `terraform_drift.yml`, `secret-scanning.yml`, `docs.yml`, `pr_acceptance_criteria.yml`. Run locally: `make ci` (uses `act`).

### Terraform runs in CI (FLIP#962)

`validate_terraform.yml` does the credential-free `fmt`/`validate` pass. On top of that, three
OIDC-authenticated workflows drive real state — no long-lived AWS keys in GitHub: `terraform_plan.yml`
(plan staging on every PR touching `deploy/providers/AWS/**`), `terraform_apply.yml` (push to
`develop` → stag, push to `main` → **prod**), and `terraform_drift.yml` (nightly plan, one issue per
environment). **Merging to `main` now changes production infrastructure** — the previous "don't
`make apply` for prod" rule is superseded.

Two guards make the unattended apply safe (`resolve-image-tags.sh` pins this commit's sha tag;
`check-fl-plan-impact.sh` holds any apply that would kill an in-flight training run), and Terraform
inputs reach CI through `scripts/compose-ci-env.sh` — so **adding an `export TF_VAR_…` line means
also updating that script's manifest, all three workflow `env:` blocks, and both GitHub
environments**. Full detail: [`deploy/providers/AWS/CLAUDE.md`](deploy/providers/AWS/CLAUDE.md#terraform-ci-flip962).

### Docker image builds: gated on tests, manual trigger for branches

**The application `docker_build_*.yml` workflows (`flip_api`, `trust_trust_api`, `trust_imaging_api`,
`trust_data_access_api`, `omop_db`) auto-publish to GHCR only after their service's test workflow
passes on `develop` or `main`** — they trigger via `workflow_run` and a job-level `if` gates on
success, so a red test suite never publishes. Path filtering is inherited from the test workflow, so a
build still only fires when that service changed. (`orthanc` and `xnat_*` keep their direct push
trigger, having no separate test workflow; `orthanc` runs an in-job auth smoke test instead. `flip-ui`
is a CI smoke test that never publishes — it is rebuilt locally by `make deploy-ui` and never consumed
from GHCR; the rest are.) Every publish also pushes an immutable **`sha-<short7>`** tag that
hub ECS deploys pin — see
[`deploy/providers/AWS/CLAUDE.md`](deploy/providers/AWS/CLAUDE.md#image-tags-deploys-and-the-fl-quiesce)
for deploys, rollback and the FL quiesce reminder.

Branch pushes do NOT build images. If you pin a branch-named tag in a compose file for prod testing,
trigger the build manually (`workflow_dispatch` bypasses the test gate) and wait for green before
redeploying:

```bash
gh workflow run docker_build_flip_api.yml --ref <branch-name>
gh run list --workflow=docker_build_flip_api.yml --branch <branch>
```

> **Note:** `workflow_run` triggers only take effect once these workflow files are on the repo's
> **default branch**. The first merge that introduces them won't retroactively publish.

## Pre-commit Hooks

TruffleHog, detect-secrets, large file check (max 1000KB), merge conflict markers, YAML validation, private key detection, env var validation, fl-apps required-files generation (`fl-apps-required-files` — regenerates each `fl-apps/<backend>/required_files.json` from its per-template arrays via `fl-apps/check_required_files.sh`; rewrites-and-fails on drift like `prettier`, so re-stage and commit again — backstopped by the `check-required-files` CI workflow. The aggregate is `linguist-generated` in `.gitattributes`; never hand-edit it — edit the per-template `required_files.json`), uv lockfile sync (`uv-lock`, one entry per uv project). Install: `pre-commit install`.

## Security Rules

- Never commit secrets/credentials (pre-commit hooks enforce this).
- SSH-over-SSM mandatory (no port 22 exposed).
- Never bypass TLS (`curl -k` prohibited).
- Use `AES_KEY_BASE64` for trust communication encryption (AES-256-GCM envelope; see the env var entry above for the key-match and flag-day rules).
- AWS Cognito for hub auth, per-trust API keys for trust-to-hub auth.
- Internal service key for fl-server-to-hub auth (separate from trust keys).
- Trust-internal service key for trust-api / imaging-api / fl-client → imaging-api / data-access-api auth (per-trust, never leaves trust env). See **Trust-internal Service Authentication** below.
- FL clients intentionally have no Central Hub credentials.
- Each fl-client mounts only its own net's slice of the images tree (`<BASE_IMAGES_DOWNLOAD_DIR>/net-N`, at the unchanged container path `/app/data/images/net-N`) so training code cannot read another net's cached studies; imaging-api keeps the whole-tree mount. The slice is what bounds the imaging-api download cache below: cached studies now survive across rounds (and, on Flower, across jobs — it has no `CleanupImages`), so a whole-tree mount would hand every client a durable copy of every other net's cohort rather than a within-job one. The `net-N` mount sources must be pre-created writable by imaging-api's uid and the client (which also writes: `flip.add_resource` staging, NVFLARE `CleanupImages`): trust `Makefile` `$(ensure_net_dirs)` (ownership-aware — the on-prem bring-up runs under sudo), AWS `site.yml`, the on-prem `site_local_trust.yml`, K8s `images-init`. A Docker/kubelet/sudo-created root-owned mount source breaks those writes (downloads 500 → `num_samples=0`). Ownership is **backend-aware**: NVFLARE's client shares imaging-api's uid so owner write suffices (`0755`), but Flower's is built on upstream `flwr/base` and runs as `app` (uid/gid 49999), so its `net-N` dirs are group-writable — group `49999` + `0775` on the prod/on-prem/K8s paths, the host GID + `0775` in dev (where the compose already `group_add`s it). All four pre-creation paths fail loudly rather than warn when they cannot apply this.
- Cohort-query validation is three-layer and **deliberately asymmetric — do not "sync" the layers**. Only the trust-side `data_access_api.services.cohort.validate_query` is authoritative (single parse-validate-emit; length, single-statement, SELECT-only, no `INSERT`/`UPDATE`/`DELETE`/`MERGE` anywhere in the tree — a writable CTE parses as a top-level `Select`, so the shape check alone misses it — `omop`-schema pin, literal `LIMIT`/`OFFSET`; re-emits from the checked AST; backed by the read-only `data_analyst_reader` role — pass its return value to the engine, never the caller's raw string). The hub-side `flip_api.cohort_services.submit_cohort_query.validate_query` is a *fast-feedback validity pre-check only, not a security control*: it exists so a malformed query fails in-hand instead of after an async fan-out to every trust, and enforces only what every trust would reject anyway. The flip-ui cohort form validates required-field only. A trust must stay safe regardless of what the hub checked, so hub drift is safe by construction. **No layer uses a keyword denylist** — the removed one blocked legitimate `SUBSTRING()` while stopping nothing; blind extraction is defeated by the literal-`LIMIT` rule and DDL/DML by the read-only role. See [`trust/data-access-api/README.md`](trust/data-access-api/README.md#cohort-query-validation).
- Row-level cohort egress is gated on `COHORT_QUERY_THRESHOLD` at **both** row-level routes — `/cohort/dataframe` (FL training data) and `/cohort/accession-ids` (the accession list that decides whose imaging is pulled into XNAT) — sharing one fixed refusal string so a below-threshold cohort is indistinguishable from an empty one. The threshold counts **distinct subjects, not rows**: the floor exists to stop a response revealing that ">=1 patient matched", and rows only stood in for patients while every cohort was one row per person — one row per imaging study (ten X-rays from one patient) and tabular projects (FLIP#1071) both broke that. `count_distinct_subjects` resolves subjects from `person_id` directly, else from `accession_id` through `omop.image_occurrence`; a cohort exposing neither is refused, as a 400 naming the column on `/cohort/dataframe` (query shape, not contents, so it is safe to be specific) and as the ordinary indistinguishable 403 on `/cohort/accession-ids`. The threshold is the trust's own disclosure floor (default 10, set per trust in its kit file), enforced trust-side rather than relying on the hub's staging guard. Both gates evaluate the **live** cohort on every call: FLIP stores the cohort only as a SQL string and re-runs it against OMOP at every stage, so a project can import cleanly and later start refusing (FLIP#857).
- Do not hardcode env values in Dockerfiles or compose files.
- 72-hour supply-chain cooldown on Python/npm package installs — enforced by uv `exclude-newer` (`[tool.uv]` in every `pyproject.toml`) and npm `min-release-age` (`flip-ui/.npmrc`, requires npm >= 11.10 which Node 24 LTS ships), backstopped by a `uv lock --check` CI gate in `secret-scanning.yml`. See CONTRIBUTING.md ("Dependency cooldown").

## Trust-internal Service Authentication

Every trust-internal call (trust-api, imaging-api, fl-client → imaging-api / data-access-api) carries
a shared-secret header: name from `TRUST_INTERNAL_SERVICE_KEY_HEADER`, value the per-trust
`TRUST_INTERNAL_SERVICE_KEY` from that trust's kit file, compared with `hmac.compare_digest`. Each
trust gets a distinct key and the hub never sees it. `/health` stays unauthenticated. User-uploaded
training code never handles the header — it calls `flip.*` and the package forwards it.

Threat model, per-service code paths and key rotation:
[`trust/CLAUDE.md`](trust/CLAUDE.md#trust-internal-service-authentication).

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
