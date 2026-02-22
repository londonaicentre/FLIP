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

<p align="left">
<img src="docs/images/flip-logo.png" height="200" alt='flip-logo' />
</p>

Federated Learning Interoperability Platform

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)
... <!-- TODO add more badges here (citations, etc) -->

FLIP is an open-source platform for federated training and evaluation of medical imaging AI models across healthcare institutions, while ensuring data privacy and security.

FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) in collaboration with Guy's and St Thomas' NHS Foundation Trust and King's College London.

## Docker Deployment Setup

This repository consolidates all the flip services in a mono repository so they can all be deployed in a single
docker compose file for local testing and development.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with [Swarm mode](https://docs.docker.com/engine/swarm/) initialized
- [Nvidia Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [Make](https://formulae.brew.sh/formula/make)
- [Python 3.10+](https://www.python.org/downloads/)
- [UV](https://docs.astral.sh/uv) - Python environment management tool
- postgresql-client and postgresql-client-common (install with `apt install postgresql-client postgresql-client-common` on Debian/Ubuntu)

Optional Tools:

- [act](https://github.com/nektos/act) - A tool to run GitHub Actions locally
- [Homebrew](https://brew.sh/) - A package manager for macOS and Linux (optional, but recommended for installing `act`)
- [VSCode](https://code.visualstudio.com/) - A code editor with support for remote development
- [Postman](https://www.postman.com/) - A tool for testing APIs

### Optional Tools

#### Recommended VSCode Extensions

The file [`recommended_extensions.vsix`](recommended_extensions.vsix) contains a list of recommended VSCode extensions
for the flip project. You can install them by running the following command in your terminal:[]

```bash
code --install-extension recommended_extensions.vsix
```

These are particularly useful for keeping the code style consistent across the project, helping with debugging, and
providing a better development experience.

The most critical extensions is `ms-vscode-remote.vscode-remote-extensionpack`, which allows you to connect to the
Docker container running the flip services and edit the code inside the container. This is useful for debugging
and testing the services without having to rebuild the Docker image every time you make a change.
Normally we develop on a remote server, so we use the `Remote - SSH` extension to connect to the server and then use the
`Remote - Containers` extension to connect to the Docker container running the flip services. This minimizes the
overhead of testing things in your local machine and then deploying them to the server.

#### Auxiliary Tools

- [beekeeper](https://www.beekeeperstudio.io/) - A cross-platform SQL editor and database manager that can be used to
  manage the database used by the flip services. It is useful for verifying the data in the database and running
  SQL queries.
- [postman](https://www.postman.com/) - A API testing tool that can be used to test the APIs exposed by the
  flip services. It is useful for verifying the functionality of the APIs and testing different scenarios. You can
  create collections of requests and run them in different environments. It is also useful for testing the APIs.

### First run

To be able to pull the FLIP docker images, configure your ghcr.io credentials as follows:

1. Make sure you can see the images in [https://github.com/londonaicentre/FLIP/packages](https://github.com/londonaicentre/FLIP/packages) and [https://github.com/londonaicentre/flip-fl-base/packages](https://github.com/londonaicentre/flip-fl-base/packages)
   - Contact a team member to be given permission if you cannot.
2. Create a [Github public access token (classic)](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic) with these permissions:
   - `repo`
   - `admin:org`
   - `read:packages`
3. Log in using either
   1. `docker login ghcr.io -u <github_username>`, entering your token when prompted, or
   2. `echo <token> | docker login ghcr.io -u <github_username> --password-stdin`

### Using the Makefile

To start the services, you can use the Makefile provided in the root directory. The Makefile provides several convenient commands to manage the services defined in the `deploy/compose.yml` file.

For example:

| Command | Description |
|---------|-------------|
| `make up` | Run all services using Docker Swarm for XNAT (⚠️ This will not build the images, use `make build` first if needed)|
| `make up-no-trust` | Run all services except the trust services related services |
| `make up-trusts` | Run the trust services related services (uses Docker Swarm for XNAT) |
| `make central-hub` | Run the central API service, including the database and UI |
| `make central-fl` | Run the FL API service |
| `make build` | Build all Docker images |
| `make down` | Stop all services and remove the containers (including Swarm stacks) |
| `make restart` | Stop and start all services |
| `make restart-no-trust` | Stop and start all services except the trust services related services |
| `make clean` | Remove all stopped containers, networks, and images |
| `make ci` | Run the CI pipeline locally using `act` |
| `make tests` | Run the tests for all services |

You can add new commands to the Makefile to create smaller deployments for testing and development.

### Docker Swarm Deployment

The XNAT services are deployed using Docker Swarm mode for better resource management and scalability. Docker Swarm is automatically used when running `make up` or `make up-trusts`.

**Key features of Swarm deployment:**

- Better resource allocation with CPU and memory limits
- Automatic service recovery with restart policies
- Overlay networking for secure service communication
- Support for multi-node deployment (if configured)

**Swarm-specific commands:**

- XNAT services are deployed as Docker stacks (`xnat1` and `xnat2`)
- The Swarm deployment uses the [trust/xnat/xnat-docker-compose/docker-compose-stack.yml](trust/xnat/xnat-docker-compose/docker-compose-stack.yml) file
- Networks are created as overlay networks with `--attachable` flag for flexibility

**Note:** Docker Swarm mode must be initialized on your system. If not already initialized, run:

```bash
docker swarm init
```

After that, you will need to restart the docker networks used by the services:

there is a command to create the networks, but you will need to remove them manually first if they are already running:

```bash
docker network rm deploy_trust-network-1
docker network rm deploy_trust-network-2
```

Then create the networks again:

```bash
make create-networks
```

To manually manage XNAT Swarm services:

```bash
# Start XNAT services in Swarm mode
cd trust/xnat
make up-swarm

# Stop XNAT services in Swarm mode
make down-swarm

# Get a shell in the XNAT container (Swarm mode)
make xnat-shell-swarm
```

### Basic Usage

To start the development environment:

```bash
make up
```

This will start all the services defined in the `deploy/compose.yml` file. The services will be started in detached
mode, so you can continue using your terminal. Use `docker compose ps` to see the status of the services and see which
ports they are running on.

To get a shell some of the services, you can run:

```bash
docker compose -f deploy/compose.yml exec < service-name > < command >
```

For example:

```bash
docker compose -f deploy/compose.yml exec flip-ui /bin/sh
```

This will give you a shell in the `flip-ui` container. You can run any command inside the container, including
installing new packages, running tests, and debugging the code.

Some aliases are defined in the Makefile to make this easier:

```bash
make flip-ui-shell
```

```bash
make down
```

If you want to run a single service you can run:

```bash
docker compose -f deploy/compose.yml run --rm < service name >
```

### Federated Learning Setup

The project supports [NVIDIA FLARE](https://developer.nvidia.com/flare) and [Flower Framework](https://flower.ai/) for federated learning. FLARE requires provisioned certificates and configuration files that are generated in the separate repository [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) (see that repository for instructions on how to provision the workspace).

#### FL_PROVISIONED_DIR Configuration

The `FL_PROVISIONED_DIR` environment variable points to the NVFLARE provisioned workspace containing:

- Certificates and keys for secure communication
- `fed_client.json` and `fed_admin.json` configuration files
- Network-specific startup kits for FL APIs, FL servers, and FL clients

**Important Notes:**

1. **Repository Structure Assumption**: The system assumes `flip` and `flip-fl-base` are sibling directories:

   ```bash
   parent-directory/
   ├── flip/             # This repository
   └── flip-fl-base/     # Contains the provisioned workspace
       └── workspace/
           ├── net-1/
           └── net-2/
   ```

2. **Path Resolution**: While `.env.development` defines `FL_PROVISIONED_DIR` as a relative path (`../flip-fl-base/workspace`), the Makefile automatically converts this to an absolute path using:

   ```makefile
   override FL_PROVISIONED_DIR := $(shell realpath $(dir $(lastword $(MAKEFILE_LIST)))/../flip-fl-base/workspace)
   ```

   This ensures Docker volume mounts work correctly (Docker requires absolute paths) while maintaining portability across different machines.

3. **Why This Matters**: Docker Compose cannot resolve relative paths for volume mounts, so the absolute path conversion is essential for FL services to access their provisioned certificates and configuration files.

If you see errors like "fed_client.json does not exist" or "missing startup folder", verify that:

- The [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) repository is cloned as a sibling directory
- The workspace has been properly provisioned with NVFLARE certificates
- The `FL_PROVISIONED_DIR` path is correctly resolved (check Makefile output)

### Troubleshooting

#### Error building images

```console
make -C trust build
make[1]: Entering directory '/data/github/flip/trust'
BASE_IMAGES_DOWNLOAD_DIR_TRUST1=./data/trust-1 OMOP_DB_PORT=5434 XNAT_PORT=8104 DATA_ACCESS_API_PORT=8010 TRUST_DEBUG_PORT=5682 IMAGING_DEBUG_PORT=5681 DATA_ACCESS_DEBUG_PORT=5680 TRUST_NETWORK_NAME=deploy_trust-network-1 docker compose -f compose_trust.yml build
validating /data/github/flip/trust/compose_trust.yml: services.fl-client-net-1 Additional property gpus is not allowed
make[1]: *** [Makefile:53: build] Error 15
make[1]: Leaving directory '/data/github/flip/trust'
make: *** [Makefile:56: build] Error 2
```

The error `Additional property gpus is not allowed` may arise from the version of docker-compose being too low. The argument should be supported for versions >=19.03, but updating docker compose from 2.29.7 to 2.40.3 seemed to fix the error. If your `apt` does not provide the latest version you may need to remove out-of-date Docker repos from `/etc/apt/source.list.d` and reinstall.

## How to add a new service

Add a new service definition to the services section in the docker compose files. For example:

```yml
# deploy/compose.yml
services:
  # Existing flip-ui service...

  # New service example
  new:
    build:
      context: ../path/to/backend
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    volumes:
      - ../path/to/backend:/app
    depends_on:
      - database

...

```

Optionally update the Makefile to include commands for the new service:

```Makefile
# Makefile
# Start only the new service
new:
    docker compose -f deploy/compose.yml up -d new
```

## Python best practices

### Environment

- Use [UV](https://docs.astral.sh/uv) for python environment management. The UV configuration file is located at the
  `pyproject.toml` files in the root directory of each service. It contains the configuration for building the virtual
  environment, packaging the code, running the tests, and linting the code.
- The `.python-version` file is used to define the python version for the virtual environment. It is used by UV to
  create the virtual environment with the specified python version. The `.python-version` file MUST be present in the
  root directory of each service. The python version should be set to the same version as the one used in the Dockerfile.
- The `pyproject.toml` file is used to define the dependencies for the service. It is the source of truth for the
  dependencies and should be used to install the dependencies in the virtual environment. The `pyproject.toml` file
  MUST be present in the root directory of each service. The dependencies should be defined in the
  `[tool.poetry.dependencies]` section of the `pyproject.toml` file. The dependencies should be installed using the
  `uv sync` command. To add a new dependency, use the `uv add < package name >` command. This will add the dependency
  to the `pyproject.toml` file and install it in the virtual environment. You can also use the
  `uv add < package name > --dev` command to add a development dependency or the
  `uv add < package name > --group < group name >` command to add a dependency to a specific group.
- Environment variables are defined in the [`.env.development`](.env.development) file. This file is used to define the
  environment variables for the development environment. It contains dummy credentials and other environment variables
  to be used in the development environment. On production, these variables should be set in the production environment
  by following the best practices on safety and security. The `.env.development` file MUST NOT be used in production.
  **The `.env.development` file centrally defines the environment variables for all services.**
- Docker file should get their environment variables from the `.env.development` file through the docker compose file.
  This is done by using the `env_file` directive in the docker compose file. This way, the environment variables are
  available in the Docker container and can be used by the services. **Avoid using hardcoded values in the Dockerfile,
  the code, or the docker compose file.**
- **FL-Specific Environment Variables**: Federated learning services use specific environment variables:
  - `FL_PROVISIONED_DIR`: Path to NVFLARE provisioned workspace (automatically resolved to absolute path by Makefile)
  - `FL_API_PORT`: Port for FL API services (default: 8000)

### Code Style

- Use [ruff](https://docs.astral.sh/ruff) for linting and formatting. The ruff configuration file is located at the
  `pyproject.toml` files in the root directory of each service.
- Documentation follows the [Google style guide](https://google.github.io/styleguide/pyguide.html) for Python. The
  documentation generator is [Sphinx](https://www.sphinx-doc.org/en/master/).

We use these custom ruff rules to enforce the code style:

```toml
[tool.ruff]
line-length = 120

[tool.ruff.lint]
preview = true
select = ['I', 'F', 'E', 'W', 'PT']
```

Add this to the your `pyproject.toml` file to enable the custom ruff rules.

### Testing

- Use [pytest](https://docs.astral.sh/pytest) for testing. The pytest configurations are located at the `pyproject.toml`.
  The configuration  contains the configuration for running the tests, including the test discovery rules, test paths,
  and test dependencies.
- Coverage is measured using [coverage.py](https://coverage.readthedocs.io/en/7.8.0/). The coverage configuration is
  located at the `pyproject.toml` file. The configuration contains the configuration for measuring the coverage,
  including the coverage report format, coverage report paths, and coverage report dependencies. We do not use coverage
  for the PR checks, but please make sure to run it locally before pushing your changes and try to keep the coverage
  as high as possible.
- For testing APIs and other integration tests, database assets, and other resources, we use
  [pytest-fixture](https://docs.pytest.org/en/7.1.x/how-to/fixtures.html). For fixtures that are reused across multiple
  test files, we use the `conftest.py` file to import fixtures defined in files in the `tests/fixtures` directory.
  In some cases we use [`factory_boy`](https://factoryboy.readthedocs.io/en/latest/) to create test data. This is
  useful for creating random test data following the same structure as the data used in the production environment.

Add these to the `pyproject.toml` file to enable the custom pytest rules:

```toml
[tool.coverage.report]
exclude_lines = ["if __name__ == .__main__.:"]
omit = ["*.venv/*", "*/tests/*", "*/__init__.py"]

[tool.pytest.ini_options]
python_files = ["test_*.py", "*_test.py"]
addopts = []
filterwarnings = ["ignore::DeprecationWarning", "ignore::FutureWarning"]

[tool.pytest.ini_options_debug]
python_files = ["test_*.py", "*_test.py"]
filterwarnings = ["ignore::DeprecationWarning", "ignore::FutureWarning"]
```

We use Makefiles to run all the services, docker building, and testing. There is no command to run all from the top
project, but you can run the tests for each service going to the service directory and running `make test`.
A `Makefile` shall exist in each service and define the commands to run the tests.
For running python tests, the recommended command is:

```bash
uv run pytest --tb=short --disable-warnings --cov=src/ --cov-report=html --cov-report=term-missing
```

This will run the tests and generate a coverage report in HTML format. The coverage report will be generated in the
`htmlcov` directory.
We typically run linting and formatting before running the tests. This is done by running the following command:

```bash
uv run ruff check . --fix
uv run mypy .
uv run pytest --tb=short --disable-warnings --cov=src/ --cov-report=html --cov-report=term-missing
```

The `make test` command will run all these commands in order.

#### Automatically creating projects for manually testing the system

To automatically create projects for manually testing the system, you can use the `make -C flip-api create_testing_projects`
command. This command will create projects in different stages (e.g. `unstaged`, `staged`, `approved`).
To clean the environment, you can use the `make -C flip-api delete_testing_projects` command.
These are also available as vscode tasks. To run them, you click on the `Terminal > Run Task...` in VSCode top menu and
select `Create testing projects` or `Delete testing projects`, or you can use the command palette (Ctrl+Shift+P) and type
`Tasks: Run Task` to find and run the tasks.

### Project Structure

The project structure is as follows:

- `deploy`: Contains the Docker deployment files
- `docs`: Contains the documentation files
- `flip-api`: Contains the central hub API service
- `flip-ui`: Contains the UI service
- `trust`: Contains the services that would be deployed in individual trust environments.
  - `data-access-api`: Contains the data access API service
  - `imaging-api`: Contains the imaging API service
  - `omop-db`: Contains a mocked OMOP database
  - `orthanc`: Contains a mocked PACS service (uses [Orthanc](https://www.orthanc-server.com/))
  - `trust-api`: Contains the trust API service
  - `xnat`: Contains a mocked [XNAT](https://www.xnat.org/) service

### PR creation

When creating a PR, please make sure to run the tests and linting before pushing your changes.
All PRs should be associated with an issue. The issue should be created before creating the PR. The PR should be linked
to the issue.

#### Run CI pipeline locally

If your CI jobs are failing and you want to debug them locally, you can run the CI pipeline locally using the
`make ci` command. This will run all the jobs defined in the `.github/workflows/` directory by running
[`act`](https://github.com/nektos/act). This will only work if you have the `act` tool installed.
To install `act`, I recommend using [Homebrew](https://brew.sh/):

```bash
brew install act
```

#### GitHub Secrets for CI

The CI/CD pipeline requires certain GitHub repository secrets to be configured for running tests. These secrets provide
sensitive values like encryption keys and API keys. See [.github/SECRETS.md](.github/SECRETS.md) for:

- Complete list of required secrets
- How to generate and configure them
- Security best practices

For local development, copy `.env.development.example` to `.env.development` and update the placeholder values.

## Setting up the AWS configuration

Some services (e.g. `flip-api`) interact with AWS services (e.g. via `boto3`). You'll need an AWS account in the AI
Centre, talk to Lawrence to create one for you. Once you have AWS access, you need to configure your profile, see the
following instructions page:
<https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html#cli-configure-sso-configure>.

For devices without access to a browser (e.g. when using via SSH), you can configure it using the device authorization
flow (`--use-device-code` option):

```bash
aws configure sso --use-device-code
```

The new configuration should show in the `~/.aws/config` file.

To log in to AWS in a new Terminal, you can run the following command:

```bash
aws sso login
```

If you want to avoid providing the profile name every time you run an AWS command, you can set the `AWS_PROFILE`
environment variable to the profile name you want to use.

```bash
export AWS_PROFILE=<your-profile-name>
```

Granted (<https://granted.dev/>) is a tool that can facilitate the process of logging in to AWS accounts and switching
between profiles:

```bash
assume <your-profile-name>
```

## Debugging across services

Follow the [debugging guide](./DEBUG.md).
