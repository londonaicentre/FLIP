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

# Contributing to FLIP

- [Introduction](#introduction)
- [The contribution process](#the-contribution-process)
  - [Preparing pull requests](#preparing-pull-requests)
    1. [Checking the coding style](#checking-the-coding-style)
    1. [Unit testing](#unit-testing)
    1. [Where does my test go?](#where-does-my-test-go)
    1. [Signing your work](#signing-your-work)
  - [Submitting pull requests](#submitting-pull-requests)

## Introduction

Welcome to the Federated Learning Interoperability Platform (FLIP)! We're excited you're here and want to contribute. This documentation is intended for individuals and institutions interested in contributing to FLIP. FLIP is an open-source project and, as such, its success relies on its community of contributors willing to keep improving it. Your contribution will be a valued addition to the code base; we simply ask that you read this page and understand our contribution process, whether you are a seasoned open-source contributor or whether you are a first-time contributor.

### Communicate with us

We are happy to talk with you about your needs for FLIP and your ideas for contributing to the project. One way to do this is to create an issue discussing your thoughts. It might be that a very similar feature is under development or already exists, so an issue is a great starting point.

When creating issues, please use the appropriate issue template:

- [**Bug Report**](https://github.com/londonaicentre/FLIP/issues/new?template=BUG-REPORT-FORM.yml) -- for reporting bugs and unexpected behaviour
- [**Feature Request**](https://github.com/londonaicentre/FLIP/issues/new?template=FEATURE-ISSUE-FORM.yml) -- for proposing new features or enhancements
- [**Task**](https://github.com/londonaicentre/FLIP/issues/new?template=TASK-ISSUE-FORM.yml) -- for general tasks that would not require any coding.
- [**Documentation**](https://github.com/londonaicentre/FLIP/issues/new?template=DOCUMENTATION-ISSUE-FORM.yml) -- for reporting documentation issues or proposing improvements to documentation.

### Project overview

FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) in collaboration with Guy's and St Thomas' NHS Foundation Trust and King's College London. It is an open-source platform for federated training and evaluation of medical imaging AI models across healthcare institutions, while ensuring data privacy and security.

The FLIP repository is a mono-repo: it consolidates the Central Hub API, Trust APIs, UI, Docker deployment, **and**
the federated learning code (base library, FL services, and tutorials) that was previously split across the legacy
[`flip-fl-base`](https://github.com/londonaicentre/flip-fl-base) (NVFLARE) and
[`flip-fl-base-flower`](https://github.com/londonaicentre/flip-fl-base-flower) (Flower) repositories. Those repositories
still hold the provisioned NVFLARE workspaces / Flower certs consumed by the dev compose files (see
[`README.md#federated-learning-setup`](README.md#federated-learning-setup)), but the FL Python/Docker source is now here.

```bash
FLIP/
├── deploy/             # Docker deployment files
│   └── providers/
│       ├── AWS/            # Terraform for Central Hub + cloud trust (EC2)
│       ├── kubernetes/     # Helm chart for K8s trust deployment
│       └── local/          # Ansible for on-premises trust deployment
├── docs/               # Sphinx documentation
├── flip-api/           # Central Hub API service
├── flip-ui/            # UI service
├── trust/              # Services deployed in individual trust environments
│   ├── data-access-api/    # Data access API
│   ├── imaging-api/        # Imaging API
│   ├── observability/      # Observability stack (Grafana, Loki, Alloy)
│   ├── omop-db/            # Mocked OMOP database
│   ├── orthanc/            # Mocked PACS service (Orthanc)
│   ├── trust-api/          # Trust API
│   └── xnat/               # Mocked XNAT service
├── flip-utils/         # `flip` Python package — platform logic, NVFLARE components, Flower helpers
├── fl-services/        # Docker images for FL networks: fl-server, fl-client, fl-api-base, fl-base
├── fl-apps/            # FL job-type implementations / app templates (standard, evaluation, diffusion_model, fed_opt)
└── fl-tutorials/       # End-to-end tutorial examples (xray classification, spleen seg/eval, diffusion)
```

## Setting up the development environment

### Prerequisites

In addition to the [deployment prerequisites](README.md#prerequisites), you'll need the following for development:

- [Python 3.12+](https://www.python.org/downloads/)
- [UV](https://docs.astral.sh/uv) - Python environment management tool (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [act](https://github.com/nektos/act) - Run GitHub Actions locally (install via [Homebrew](https://brew.sh/): `brew install act`)
- **GHCR login** — `make up` pulls the repo-built service images from GitHub Container Registry by default, so authenticate once with a PAT that has `read:packages`:
  ```bash
  echo "$GHCR_PAT" | docker login ghcr.io -u <your-github-username> --password-stdin
  ```
  Building everything locally instead (no GHCR access needed) is `make up BUILD=true` — see [Running the stack](#running-the-stack-pull-vs-build) below.

### Recommended IDE Setup

The file [`recommended_extensions.vsix`](recommended_extensions.vsix) contains a bundle of recommended VS Code
extensions for FLIP development. Install with:

```bash
code --install-extension recommended_extensions.vsix
```

Key extensions include:

- `ms-vscode-remote.vscode-remote-extensionpack` — connect to Docker containers and remote servers via SSH for in-container development, avoiding the need to rebuild images on every change
- Python linting/formatting (ruff, mypy)
- Docker tooling

Other useful tools:

- [Postman](https://www.postman.com/) — API testing
- [Homebrew](https://brew.sh/) — package manager for macOS/Linux

### Python environment management

FLIP uses [UV](https://docs.astral.sh/uv) for all Python services. Each service has a `pyproject.toml` and a
`.python-version` file in its root directory.

To install dependencies for a service:

```bash
uv sync
```

To add a new dependency:

```bash
uv add <package-name>            # runtime dependency
uv add <package-name> --dev      # development-only dependency
uv add <package-name> --group <group>  # dependency in a named group
```

The `pyproject.toml` file is the source of truth for dependencies. The Python version in `.python-version` must match
the version used in the service's Dockerfile.

### Dependency cooldown (supply-chain protection)

Recent npm and PyPI supply-chain attacks follow a consistent pattern: a maintainer's credentials are compromised, a
malicious release is published, the community detects it, and the package is yanked — usually within a few hours. To
keep poisoned releases out of FLIP's CI, developer machines, and Trust-side containers, FLIP enforces a **72-hour
cooldown** on dependency installs:

> No FLIP build, CI or local, may install a Python or JavaScript package whose release timestamp on its upstream
> registry (PyPI / npm) is less than 72 hours old. This applies to direct **and** transitive dependencies.

The policy is enforced through native package-manager configuration:

- **uv (Python)** — every `pyproject.toml` sets `tool.uv.exclude-newer = "3 days"`, so `uv lock` and `uv add` never
  resolve a release younger than 72 hours (the `uv.lock` records this as a rolling `exclude-newer-span`). The
  **Dependency Cooldown Check** job in [`secret-scanning.yml`](.github/workflows/secret-scanning.yml) runs
  `uv lock --check` on every project, failing CI if a lockfile drifts from its manifest or was generated under a
  wider `exclude-newer` window than the committed manifest allows.
- **npm (JavaScript)** — `flip-ui/.npmrc` sets `min-release-age=3`, so `npm install` refuses to resolve a release
  younger than 72 hours. This key was introduced in npm 11.10, so `flip-ui/Dockerfile` and the `test_flip_ui.yml`
  workflow use Node 24 LTS (which ships npm >= 11.10); Node 22 LTS bundles npm 10.x and silently ignores the key.
  CI installs use `npm ci`, which fails on any `package-lock.json` / `package.json` mismatch. npm only enforces
  `min-release-age` at lockfile-write time (`npm install <pkg>`), not when installing from a pinned
  `package-lock.json`, so the npm cooldown rests on `.npmrc` rather than a CI gate.

There is no automated dependency-update bot wired into the repo today. Dependency bumps are hand-rolled PRs; the
two layers above (uv `exclude-newer` and npm `min-release-age` at install time, `uv lock --check` in CI) catch a
too-fresh package regardless of how it arrived in the lockfile.

The cooldown applies automatically when you run `uv add <package>` or `npm install <package>` — a release younger
than 72 hours is simply not selected. Run `make lock` to refresh every `uv.lock` after a dependency change.

#### Emergency override

For a genuine same-day patch of an active CVE, the cooldown can be bypassed for the single package that needs it:

- **uv** — add an `exclude-newer-package` entry under `[tool.uv]` for that package (for example
  `exclude-newer-package = { "<package>" = "<recent-timestamp>" }`) and re-run `uv lock`. The entry is committed, so
  the exception is visible in the pull request and `uv lock --check` still passes.
- **npm** — run `npm install <package> --min-release-age=0`, which overrides the `.npmrc` setting for that one
  command.

Any override must be justified in the pull-request description. Use it only for security patches that genuinely
cannot wait 72 hours.

### Environment variables

Environment variables for local development are defined in [`.env.development.example`](.env.development.example). This file uses
dummy/safe credentials for local use and **must not be used in production**. It centrally configures all services.

To get started, copy the example file:

```bash
cp .env.development.example .env.development
```

Then generate the internal service key (also generated automatically by `make up`):

```bash
make generate-internal-service-key
```

This writes `INTERNAL_SERVICE_KEY` with `INTERNAL_SERVICE_KEY_HASH` into `.env.development` for
fl-server-to-hub authentication.

Trusts are registered on the **running hub** with `make register-trusts` (shipped dev roster) or
`make register-trust KIT=<CODE>` (one trust), which inserts each `trust` row (with its
`api_key_hash`), claims an FL kit slot, and fills that trust's kit file `trust/.env.<CODE>.<env>`
carrying `TRUST_API_KEY` and `TRUST_INTERNAL_SERVICE_KEY`. `make up` runs `register-trusts`
automatically once the hub is up.

Docker services receive these variables via the `env_file` directive in the
compose file — avoid hardcoding values in Dockerfiles or compose files directly.

**Authentication environment variables:**

- `TRUST_API_KEY_HEADER` — HTTP header name for trust-to-hub authentication.
- `TRUST_API_KEY` — single per-trust plaintext API key. Lives only in that trust's kit file
  (`trust/.env.<CODE>.<env>`), written by `make register-trusts`; never on the hub.
- `INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for fl-server-to-hub authentication.
- `INTERNAL_SERVICE_KEY` — internal service key used by the fl-server on the Central Hub.
- `INTERNAL_SERVICE_KEY_HASH` — hub-side SHA-256 hash of the internal service key.
- `TRUST_INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for trust-internal service auth (default
  `X-Trust-Internal-Service-Key`). Sent by every caller (trust-api, imaging-api, fl-client) on every
  call to imaging-api or data-access-api.
- `TRUST_INTERNAL_SERVICE_KEY` — single per-trust plaintext key, in that trust's kit file
  (`trust/.env.<CODE>.<env>`), minted by `register_trust`. Read by every trust-internal container. The hub
  never sees it. Distinct from `INTERNAL_SERVICE_KEY*` (which protects fl-server → flip-api on the
  Central Hub). See [`CLAUDE.md`](CLAUDE.md#trust-internal-service-authentication) for the threat model.

FL clients (trust side) intentionally do **not** receive Central Hub API credentials. Only the fl-server (on the Central
Hub) communicates with flip-api. FL clients relay metrics and exceptions to the fl-server, which forwards them.

**FL-specific environment variables:**

- `FL_PROVISIONED_DIR` — path to the NVFLARE or Flower provisioned workspace. The Makefile automatically converts this to an absolute path (Docker requires absolute paths for volume mounts). This directory contains certificates, keys, `fed_client.json`, and other files generated during provisioning for each network. For the NVFLARE implementation, this is typically `../flip-fl-base/workspace`. For the Flower implementation, this is typically `../flip-fl-base-flower/certs`.
- `FL_API_PORT` — port for FL API services (default: `8000`).

### Setting up AWS access

Some services (e.g. `flip-api`) interact with AWS via `boto3`. You will need AWS credentials configured locally.

Configure AWS SSO:

```bash
aws configure sso
```

For headless/SSH environments, use the device authorization flow:

```bash
aws configure sso --use-device-code
```

Log in to AWS in a new terminal session:

```bash
aws sso login --profile <your-profile-name>
```

To avoid specifying the profile name on every command:

```bash
export AWS_PROFILE=<your-profile-name>
```

### GitHub Secrets for CI

The CI/CD pipeline requires GitHub repository secrets to run tests and deployments. See
[.github/SECRETS.md](.github/SECRETS.md) for the complete list, how to generate them, and security best practices.

### Running the CI pipeline locally

To debug failing CI jobs without pushing, use `act` (requires Docker):

```bash
make ci
```

This runs all jobs defined in `.github/workflows/` locally.

### Running the stack (pull vs. build)

In development (`PROD` unset), `make up` **pulls** the repo-built service images
(`flip-api`, `trust-api`, `imaging-api`, `data-access-api`, `orthanc`) from GHCR
instead of building them — each carries `image:` + `pull_policy: always` in the dev
compose, and your local `src/` is bind-mounted on top, so editing a `.py` still
hot-reloads against the pulled image. Startup is fast and matches the published
`:stag` artifact's environment.

```bash
make up                # pull GHCR images (default; requires `docker login ghcr.io`)
make up BUILD=true     # rebuild the repo-built services from local source instead
```

Use `BUILD=true` when you've changed **dependencies** (`uv.lock`/`pyproject.toml`),
system packages, or a `Dockerfile` — those live in the image layer, so a plain
`make up` (which pulls) won't pick them up. `flip-ui` always builds locally (it has
no GHCR image). Stag/prod (`PROD=stag|true`) are unaffected: they run the prod
compose with baked images and no bind-mounts.

## The contribution process

*Fork the repository before making changes* [Learn how to fork](https://help.github.com/en/github/getting-started-with-github/fork-a-repo). All contributions to the `develop` branch must be made via pull requests. This allows us to review your changes and ensure they meet our quality standards before merging them into the main codebase.

*Pull request early*, *commit often*. Don't wait until your changes are perfect before creating a pull request.
Commit your changes in small, logical chunks with clear commit messages. This makes it easier for reviewers to understand your changes and provide feedback.

We encourage you to create pull requests early. It helps us track the contributions under development, whether they are ready to be merged or not. [Create a draft pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/changing-the-stage-of-a-pull-request) until it is ready for formal review.

### Preparing pull requests

To ensure code quality, FLIP relies on linting tools ([ruff](https://docs.astral.sh/ruff)), static type analysis ([mypy](https://github.com/python/mypy)), as well as a set of unit and integration tests.

This section highlights all the necessary preparation steps required before sending a pull request. To collaborate efficiently, please read through this section and follow them. Make sure you configure your coding environment to follow the configurations in the `pyproject.toml` files so these are automatically enforced.

- [Checking the coding style](#checking-the-coding-style)
- [Unit testing](#unit-testing)
- [Signing your work](#signing-your-work)

#### Checking the coding style

FLIP uses [ruff](https://docs.astral.sh/ruff) for both linting and formatting. The ruff configuration is defined in the `pyproject.toml` file at the root of the repository and in each service directory.

The project-wide ruff rules are:

```toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
preview = true
select = ['I', 'F', 'E', 'W', 'PT', 'UP006', 'UP007', 'UP035', 'UP045']
```

We also use [mypy](https://github.com/python/mypy) for static type checking.

Before submitting a pull request, ensure all linting passes by running the following commands from the relevant service directory:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run type checking
uv run mypy .
```

Most services have a `Makefile` with a `test` target that runs linting, type checking, and tests in sequence. For example, from a Python service directory:

```bash
make test
```

The `flip-ui` service uses `make unit_test` (Vitest) instead of `make test`.

Documentation follows the [Google style guide](https://google.github.io/styleguide/pyguide.html) for Python docstrings.

If your PR contains code inspired by other code bases, you MUST inform us in your PR so we can add proper references to the original code and evaluate whether it can be incorporated into our License framework.

#### Unit testing

*If it's not tested, it's broken*, so all new functionality should be accompanied by an appropriate set of tests. Existing tests throughout the services can serve as examples.

FLIP uses [pytest](https://docs.pytest.org/) for testing and [coverage.py](https://coverage.readthedocs.io/) for measuring code coverage.

Tests are located within each service's directory (e.g. `flip-api/tests/`, `trust/trust-api/tests/`). Test file names follow the `test_[module_name].py` or `[module_name]_test.py` convention.

To run tests for a specific service, navigate to the service directory and run:

```bash
uv run pytest --tb=short --disable-warnings --cov=src/ --cov-report=html --cov-report=term-missing
```

Or use the Makefile shorthand:

```bash
make test
```

This will run ruff, mypy, and pytest in sequence. The coverage report is generated in HTML format in the `htmlcov` directory.

To run unit tests across all services in the main repository from the root:

```bash
make unit_test
```

`make tests` is a narrower target that runs `flip-ui` unit and Cypress e2e tests followed by the full `flip-api`
test suite (ruff, mypy, and pytest).

For the migrated FL base library (now in `flip-utils/`), unit tests can be run with:

```bash
cd flip-utils && uv run pytest tests/unit -s -vv
```

(The inherited `make unit-test` target documented in [`flip-utils/README.md`](flip-utils/README.md) is part of the
still-in-progress reconciliation called out at the top of that README — `flip-utils/` does not yet ship a Makefile in
this mono-repo.) See [`flip-utils/README.md`](flip-utils/README.md) (the "Unit Tests" / "Integration Testing"
sections — subject to the in-progress reconciliation noted there) for the FL package's tests, and
[`fl-services/README.md`](fl-services/README.md) for provisioning FL networks.

**Kubernetes chart testing**: The K8s Helm chart at `deploy/providers/kubernetes/` can be tested with:

```bash
# Lint + render + schema validation
make -C deploy/providers/kubernetes test

# Render all FL backend variants
make -C deploy/providers/kubernetes template-all-backends

# Validate rendered templates against K8s schema (requires kubeconform)
make -C deploy/providers/kubernetes validate
```

The chart has a `check_status.py` smoke test script and a `register_k8s_trust.py` registration script. See the [K8s README](deploy/providers/kubernetes/README.md) for details.

**Testing fixtures**: For testing APIs and integration tests, we use [pytest fixtures](https://docs.pytest.org/en/latest/how-to/fixtures.html). Shared fixtures are defined in `conftest.py` files. In some cases, [`factory_boy`](https://factoryboy.readthedocs.io/) is used to create test data following production data structures.

All new functionality should be accompanied by an appropriate set of tests. Existing tests throughout the services can serve as examples.

Add these sections to the service's `pyproject.toml` to configure pytest and coverage:

```toml
[tool.coverage.report]
exclude_lines = ["if __name__ == .__main__.:"]
omit = ["*.venv/*", "*/tests/*", "*/__init__.py"]

[tool.pytest.ini_options]
python_files = ["test_*.py", "*_test.py"]
addopts = []
filterwarnings = ["ignore::DeprecationWarning", "ignore::FutureWarning"]
```

#### Where does my test go?

A test belongs in `tests/integration/` **if and only if it touches a real backing service**. Examples of "real backing service":

- A real Postgres (via the `session` fixture or Testcontainers)
- A real AWS service (S3, Cognito, SES)
- A running sibling API (trust-api, data-access-api, etc.) reachable over HTTP
- A real Orthanc / XNAT / OMOP fixture

If your test mocks **all** external dependencies (database session, HTTP client, AWS clients, sibling APIs), it's a unit test — put it in `tests/unit/`, mirroring the source layout (e.g. tests for `src/flip_api/user_services/set_user_roles.py` go in `tests/unit/user_services/test_set_user_roles.py`).

FastAPI `TestClient` on its own does **not** make a test "integration" — what matters is whether the dependencies it transitively hits are real or mocked. A `TestClient`-based test that overrides every dependency (via `app.dependency_overrides`) and patches the DB session is a unit test; one that runs against an un-overridden real Postgres is an integration test.

This rule applies across all services: `flip-api/tests/`, `trust/trust-api/tests/`, `trust/imaging-api/tests/`, `trust/data-access-api/tests/`, etc.

##### flip-api: real-Postgres integration tests via Testcontainers

`flip-api/tests/integration/` boots a throwaway `postgres:16-alpine` container per pytest session via [testcontainers-python](https://github.com/testcontainers/testcontainers-python) (`tests/integration/conftest.py`). The fixture builds the schema from `SQLModel.metadata`, seeds permissions / roles / role-permissions once, and truncates per-test tables between tests. Both the existing `session` fixture and FastAPI's `Depends(get_session)` are rewired at the throwaway DB, so a new test only needs to request `session` (raw SQL access) and/or `client` (`TestClient` against the same DB) — no per-test setup required.

CI runs these via `make integration_test` from `flip-api/`. Docker is preinstalled on `ubuntu-latest`, so no `services:` block is needed in the workflow. AWS-backed integration tests (Cognito, S3, SES) are out of scope for this fixture and are skip-marked at the file level until ticket B2 lands.

#### Signing your work

FLIP enforces the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) on all pull requests. All commit messages should contain the `Signed-off-by` line with an email address.

Git has a `-s` (or `--signoff`) command-line option to append this automatically to your commit message:

```bash
git commit -s -m 'a new commit'
```

The commit message will be:

```bash
    a new commit

    Signed-off-by: Your Name <yourname@example.org>
```

### Submitting pull requests

All code changes to the `develop` branch must be done via [pull requests](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/proposing-changes-to-your-work-with-pull-requests). All PRs should be associated with an issue.

1. Create a new ticket or take a known ticket from [the issue list](https://github.com/londonaicentre/FLIP/issues).
1. Check if there's already a branch dedicated to the task.
1. If the task has not been taken, [create a new branch](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/creating-a-pull-request-from-a-fork)
named `[ticket_id]-[task_name]`.
For example, branch name `19-ci-pipeline-setup` corresponds to issue #19.
The new branch should be based on the latest `develop` branch.
1. Make changes to the branch ([use detailed commit messages if possible](https://chris.beams.io/posts/git-commit/)).
1. Make sure that new tests cover the changes and the changed codebase passes all tests locally (see [Unit testing](#unit-testing)).
1. Run linting and type checking before pushing (see [Checking the coding style](#checking-the-coding-style)).
1. [Create a new pull request](https://help.github.com/en/desktop/contributing-to-projects/creating-a-pull-request) from the task branch to the `develop` branch, with a detailed description of the purpose of this pull request.
1. Check [the CI/CD status of the pull request](https://github.com/londonaicentre/FLIP/actions), make sure all CI/CD tests pass.
1. Wait for reviews; if there are reviews, make point-to-point responses, make further code changes if needed.
1. If there are conflicts between the pull request branch and the `develop` branch, pull the changes from `develop` and resolve the conflicts locally.
1. Reviewer and contributor may have discussions back and forth until all comments are addressed.
1. Wait for the pull request to be merged.

## Cutting a release

Releases are cut from `main`. The version is set in the root `pyproject.toml`, and merging to `main` triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which reads that version, creates a `v<MAJOR.MINOR.PATCH>` git tag, and publishes a GitHub Release with auto-generated notes. On the same merge, the per-service `.github/workflows/docker_build_*.yml` workflows rebuild every service and push the `:prod` image tag (alongside `:<sha>`) to GHCR. There is no separate release-publishing step beyond merging to `main`.

### Versioning

FLIP follows [Semantic Versioning](https://semver.org/). The version in the **root** [`pyproject.toml`](pyproject.toml) is the FLIP release version — it is what `release.yml` reads to create the git tag.

Each service has its own version string:

- [`flip-api/pyproject.toml`](flip-api/pyproject.toml)
- [`flip-ui/package.json`](flip-ui/package.json)
- [`trust/trust-api/pyproject.toml`](trust/trust-api/pyproject.toml)
- [`trust/imaging-api/pyproject.toml`](trust/imaging-api/pyproject.toml)
- [`trust/data-access-api/pyproject.toml`](trust/data-access-api/pyproject.toml)

These are **independent**. Bump a service's version only when *that service* has user-visible changes, applying SemVer to the service alone. Services are not aligned with the root version on every release — a release where only `flip-ui` changed bumps the root and `flip-ui/package.json`, and nothing else. Per-service versions are informational today (deployments select images by branch via `:prod` / `:stag` tags, not by version string), but keeping them honest makes them useful for audit and changelog scope.

### Pre-release checklist

Before opening the release PR from `develop` to `main`:

- `develop` is green in [CI](https://github.com/londonaicentre/FLIP/actions).
- All PRs intended for this release are merged into `develop` and carry an appropriate label. The release-notes categories come from [`.github/release.yml`](.github/release.yml): `enhancement` / `feature`, `bug` / `fix`, `documentation` / `docs`, `ci` / `build`, `chore` / `dependencies`. PRs labelled `ignore-for-release` are excluded.
- Bump the `version` in the root `pyproject.toml` to the new release version. Additionally bump the `version` in any service file (`flip-api/pyproject.toml`, `flip-ui/package.json`, `trust/*/pyproject.toml`) whose code changed in this release, per the independent-SemVer rule above. Leave unchanged services alone.
- Run `make unit_test` and `make integration_test` locally.

### Cutting the release

1. From a branch off `develop`, commit the version bumps above and open a PR targeting `develop` with title `Release v<X.Y.Z>`.
1. Once that merges and CI is green, open a PR from `develop` to `main`.
1. On merge to `main`:
   - [`release.yml`](.github/workflows/release.yml) creates the `v<X.Y.Z>` git tag and publishes the GitHub Release with auto-generated notes.
   - Every `docker_build_*.yml` workflow under [`.github/workflows/`](.github/workflows/) rebuilds its service and pushes the `:prod` and `:<sha>` tags to GHCR.
1. Verify on the [Releases page](https://github.com/londonaicentre/FLIP/releases) that the new release exists and the notes look right. Verify on [GHCR](https://github.com/orgs/londonaicentre/packages) that the `:prod` tags on `flip-api`, `trust-api`, `imaging-api`, and `data-access-api` were updated by the latest build.

### Release notes

There is no `CHANGELOG.md` — the GitHub Releases page is the changelog. Release notes are generated automatically from PR titles and labels via [`.github/release.yml`](.github/release.yml). To curate the notes for a release, ensure each PR going into `develop` has the right label before it is merged.

### Deploying the release

Once the `:prod` images are in GHCR, deploy with:

```bash
cd deploy/providers/AWS
make full-deploy PROD=true
```

See [`deploy/providers/AWS/README.md`](deploy/providers/AWS/README.md) for full deployment instructions. For staging, no release tag is required: merging to `develop` publishes `:stag` images automatically, and `make full-deploy PROD=stag` rolls them out.

### Hotfixes

For an urgent fix on `main` without pulling in unrelated `develop` work:

1. Branch from `main`, apply the fix, bump the patch version in the root `pyproject.toml` (and any affected service).
1. Open a PR targeting `main`. On merge, the same automation kicks in — `release.yml` tags `v<X.Y.Z+1>` and `docker_build_*.yml` rebuilds `:prod`.
1. Forward-port the fix to `develop` so the next regular release includes it.

### Testing a release candidate before merge to main

Branch builds **do not** auto-publish to GHCR. To deploy a release-candidate branch for testing, manually trigger the relevant build workflows first:

```bash
gh workflow run docker_build_flip_api.yml --ref <branch-name>
gh workflow run docker_build_trust_trust_api.yml --ref <branch-name>
# ...one per service whose image you want to test
```

Wait for green completion, then point your `.env` file's `DOCKER_TAG` at the sanitized branch name (the per-service workflows publish a `:<branch>` tag on every push).

## Adding a new service

To extend the platform with a new service, add a definition to the appropriate Docker Compose file:

```yml
# deploy/compose.development.yml
services:
  new-service:
    build:
      context: ../path/to/service
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    volumes:
      - ../path/to/service:/app
    depends_on:
      - flip-db
    env_file:
      - ../.env.development
```

Create a directory following the standard service layout:

```
new-service/
├── src/
│   └── new_service/
├── tests/
├── Dockerfile
├── Makefile
├── pyproject.toml
└── .python-version
```

Optionally add Makefile shortcuts at the repository root:

```makefile
new-service:
    docker compose -f deploy/compose.development.yml up -d new-service
```

## Creating test data for manual testing

To create projects in various pipeline stages (`unstaged`, `staged`, `approved`) for manual testing:

```bash
make -C flip-api create_testing_projects
```

To clean up the test data:

```bash
make -C flip-api delete_testing_projects
```

These are also available as VS Code tasks via **Terminal > Run Task** — look for `Create testing projects` and
`Delete testing projects`.

## Documentation GIFs

The admin user-action GIFs under `docs/source/assets/admin/` (referenced from
`docs/source/sys-admin/admin-project-and-user-management.rst`) are
auto-regenerated on every push to `main` by
`.github/workflows/regenerate_docs_gifs.yml`. The workflow records the demo
Cypress specs under `flip-ui/test/cypress/docs/admin/` against a fully mocked
backend, converts the resulting videos to GIFs with `ffmpeg`, and opens a PR
against `develop` for human review.

To regenerate locally:

```bash
cd flip-ui
npm run docs:record   # records videos under test/cypress/videos/docs/admin/
npm run docs:gifs     # ffmpeg → docs/source/assets/admin/*.gif (requires ffmpeg on PATH)
```

The demo specs reuse the functional suite's fixtures and `globalIntercepts`,
and layer a CSS cursor overlay (`flip-ui/test/cypress/docs/support/demoCursor.ts`)
on top so the recorded GIFs read as visible user actions. When adding a new
admin-area UI flow that should be documented, add one demo spec under
`flip-ui/test/cypress/docs/admin/<gif-basename>.spec.ts` — the filename maps
1:1 to the output GIF name.
