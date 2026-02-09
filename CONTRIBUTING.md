<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

- [Introduction](#introduction)
- [The contribution process](#the-contribution-process)
  - [Preparing pull requests](#preparing-pull-requests)
    1. [Checking the coding style](#checking-the-coding-style)
    1. [Licensing information](#licensing-information)
    1. [Unit testing](#unit-testing)
    1. [Building the documentation](#building-the-documentation)
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

### Project overview

FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) in collaboration with Guy's and St Thomas' NHS Foundation Trust and King's College London. It is an open-source platform for federated training and evaluation of medical imaging AI models across healthcare institutions, while ensuring data privacy and security.

The project spans two repositories:

| Repository | Description |
|---|---|
| [FLIP](https://github.com/londonaicentre/FLIP) | Main mono-repo: Central Hub API, Trust APIs, UI, and Docker deployment |
| [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) | Federated learning base application built on NVIDIA FLARE (NVFLARE) |

The main FLIP repository follows a mono-repo structure with these key services:

```
FLIP/
├── deploy/             # Docker deployment files
├── docs/               # Sphinx documentation
├── flip-api/           # Central Hub API service
├── flip-ui/            # UI service
└── trust/              # Services deployed in individual trust environments
    ├── data-access-api/    # Data access API
    ├── imaging-api/        # Imaging API
    ├── omop-db/            # Mocked OMOP database
    ├── orthanc/            # Mocked PACS service (Orthanc)
    ├── trust-api/          # Trust API
    └── xnat/               # Mocked XNAT service
```

## The contribution process

*Pull request early*

We encourage you to create pull requests early. It helps us track the contributions under development, whether they are ready to be merged or not. [Create a draft pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/changing-the-stage-of-a-pull-request) until it is ready for formal review.

### Preparing pull requests

To ensure code quality, FLIP relies on linting tools ([ruff](https://docs.astral.sh/ruff)), static type analysis ([mypy](https://github.com/python/mypy)), as well as a set of unit and integration tests.

This section highlights all the necessary preparation steps required before sending a pull request. To collaborate efficiently, please read through this section and follow them. Make sure you configure your coding environment to follow the configurations in the `pyproject.toml` files so these are automatically enforced.

- [Checking the coding style](#checking-the-coding-style)
- [Licensing information](#licensing-information)
- [Unit testing](#unit-testing)
- [Building the documentation](#building-the-documentation)
- [Signing your work](#signing-your-work)

#### Checking the coding style

FLIP uses [ruff](https://docs.astral.sh/ruff) for both linting and formatting. The ruff configuration is defined in the `pyproject.toml` file at the root of the repository and in each service directory.

The project-wide ruff rules are:

```toml
[tool.ruff]
line-length = 120

[tool.ruff.lint]
preview = true
select = ['I', 'F', 'E', 'W', 'PT']
```

We also use [mypy](https://github.com/python/mypy) for static type checking.

Before submitting a pull request, ensure all linting passes by running the following commands from the relevant service directory:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run type checking
uv run mypy .
```

Each service has a `Makefile` with a `test` target that runs linting, type checking, and tests in sequence. For example, from a service directory:

```bash
make test
```

Documentation follows the [Google style guide](https://google.github.io/styleguide/pyguide.html) for Python docstrings.

#### Licensing information

All source code files should start with the following license header (respecting the comment style of each filetype):

```
Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

If your PR contains code inspired by other code bases, you MUST inform us in your PR so we can add proper references to the original code and evaluate whether it can be incorporated into our License framework.


#### Unit testing

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

To run all tests across the main repository from the root:

```bash
make tests
```

For the `flip-fl-base` repository, unit tests can be run with:

```bash
make unit-test
```

Integration tests for the FL base application are also available (see the [flip-fl-base README](https://github.com/londonaicentre/flip-fl-base#testing) for details).

**Testing fixtures**: For testing APIs and integration tests, we use [pytest fixtures](https://docs.pytest.org/en/latest/how-to/fixtures.html). Shared fixtures are defined in `conftest.py` files. In some cases, [`factory_boy`](https://factoryboy.readthedocs.io/) is used to create test data following production data structures.

*If it's not tested, it's broken*

All new functionality should be accompanied by an appropriate set of tests. Existing tests throughout the services can serve as examples.

#### Building the documentation

FLIP's documentation is located in the `docs/` directory and uses [Sphinx](https://www.sphinx-doc.org/) with the [Read the Docs theme](https://sphinx-rtd-theme.readthedocs.io/).

To build the documentation locally:

```bash
# Install documentation dependencies
cd docs
uv sync

# Build the HTML documentation
make docs

# Clean previous builds
make clean
```

The generated HTML documentation can be viewed by opening `docs/build/html/index.html` in a web browser.

Python docstrings should follow the [Google style guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings). API documentation is auto-generated using [sphinx-autoapi](https://sphinx-autoapi.readthedocs.io/).

#### Signing your work

FLIP enforces the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) on all pull requests. All commit messages should contain the `Signed-off-by` line with an email address.

Git has a `-s` (or `--signoff`) command-line option to append this automatically to your commit message:

```bash
git commit -s -m 'a new commit'
```

The commit message will be:

```
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

### Environment and dependency management

FLIP uses [UV](https://docs.astral.sh/uv) for Python environment management. Each service has its own `pyproject.toml` and `.python-version` file.

Key points:

- The `pyproject.toml` is the source of truth for dependencies.
- Use `uv sync` to install dependencies in a virtual environment.
- Use `uv add <package>` to add a new dependency.
- Use `uv add <package> --dev` for development dependencies or `uv add <package> --group <group>` for group-specific dependencies.
- The `.python-version` file defines the Python version and should match the version used in the service's Dockerfile.

Environment variables are defined centrally in the [`.env.development`](.env.development) file for the development environment. Docker services receive these variables via `env_file` in the Docker Compose file. Avoid hardcoding values in Dockerfiles, source code, or compose files.

### Makefile reference

The root `Makefile` provides commands for managing all services:

| Command | Description |
|---|---|
| `make build` | Build all Docker images |
| `make up` | Start all services (Docker Swarm for XNAT) |
| `make up-no-trust` | Start all services except trust services |
| `make up-trusts` | Start trust services only |
| `make central-hub` | Start the Central Hub API, database, and UI |
| `make down` | Stop all services and remove containers |
| `make restart` | Stop and start all services |
| `make clean` | Remove stopped containers, networks, and images |
| `make tests` | Run tests for all services |
| `make debug SERVICE=<name>` | Start debug mode for a specific service |
| `make debug-off SERVICE=<name>` | Stop debug mode for a specific service |

<p align="right">
  <a href="#introduction">Back to Top</a>
</p>
