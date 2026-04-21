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

The project spans three repositories:

| Repository | Description |
| --- | --- |
| [FLIP](https://github.com/londonaicentre/FLIP) | Main mono-repo: Central Hub API, Trust APIs, UI, and Docker deployment |
| [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) | NVIDIA FLARE federated learning base application library, workflows, and tutorials |
| [flip-fl-base-flower](https://github.com/londonaicentre/flip-fl-base-flower) | Flower federated learning base application library, workflows, and tutorials |

The main FLIP repository follows a mono-repo structure with these key services:

```bash
FLIP/
├── deploy/             # Docker deployment files
├── docs/               # Sphinx documentation
├── flip-api/           # Central Hub API service
├── flip-ui/            # UI service
└── trust/              # Services deployed in individual trust environments
    ├── data-access-api/    # Data access API
    ├── imaging-api/        # Imaging API
    ├── observability/      # Observability stack (Grafana, Loki, Alloy)
    ├── omop-db/            # Mocked OMOP database
    ├── orthanc/            # Mocked PACS service (Orthanc)
    ├── trust-api/          # Trust API
    └── xnat/               # Mocked XNAT service
```

## Setting up the development environment

### Prerequisites

In addition to the [deployment prerequisites](README.md#prerequisites), you'll need the following for development:

- [Python 3.12+](https://www.python.org/downloads/)
- [UV](https://docs.astral.sh/uv) - Python environment management tool (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [act](https://github.com/nektos/act) - Run GitHub Actions locally (install via [Homebrew](https://brew.sh/): `brew install act`)

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

### Environment variables

Environment variables for local development are defined in [`.env.development.example`](.env.development.example). This file uses
dummy/safe credentials for local use and **must not be used in production**. It centrally configures all services.

To get started, copy the example file:

```bash
cp .env.development.example .env.development
```

Then generate per-trust API keys and the internal service key (the trust keys must be done before `make up`; the
internal service key is also generated automatically by `make up`):

```bash
make generate-trust-api-keys
make generate-internal-service-key
```

These commands write all API keys directly into `.env.development`: trust plaintext keys
in `TRUST_API_KEYS` (JSON dict) with their hashes in `TRUST_API_KEY_HASHES`, and `INTERNAL_SERVICE_KEY`
with `INTERNAL_SERVICE_KEY_HASH` for fl-server-to-hub authentication. No separate key files are used.

Docker services receive these variables via the `env_file` directive in the
compose file — avoid hardcoding values in Dockerfiles or compose files directly.

**Authentication environment variables:**

- `TRUST_API_KEY_HEADER` — HTTP header name for trust-to-hub authentication.
- `TRUST_API_KEYS` — JSON dict mapping trust names to their plaintext API keys.
- `TRUST_API_KEY_HASHES` — hub-side JSON dict mapping trust names to SHA-256 hashes of their API keys.
- `INTERNAL_SERVICE_KEY_HEADER` — HTTP header name for fl-server-to-hub authentication.
- `INTERNAL_SERVICE_KEY` — internal service key used by the fl-server on the Central Hub.
- `INTERNAL_SERVICE_KEY_HASH` — hub-side SHA-256 hash of the internal service key.

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

`make tests` is a narrower target that runs `flip-ui` unit tests followed by the full `flip-api` test suite (ruff,
mypy, and pytest).

For the `flip-fl-base` repository, unit tests can be run with:

```bash
make unit-test
```

Integration tests for the FL base application are also available (see the [flip-fl-base README](https://github.com/londonaicentre/flip-fl-base#testing) for details).

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
