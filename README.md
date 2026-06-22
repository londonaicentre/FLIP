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

<p align="center"><img src="docs/source/assets/flip-logo.png" height="200" alt='flip-logo' /></p>

# Federated Learning and Interoperability Platform

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg)](https://codecov.io/gh/londonaicentre/FLIP)

[![flip-api](https://img.shields.io/badge/docker-flip--api-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/flip-api)
[![flip-ui](https://img.shields.io/badge/docker-flip--ui-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/flip-ui)

[![data-access-api](https://img.shields.io/badge/docker-data--access--api-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/data-access-api)
[![imaging-api](https://img.shields.io/badge/docker-imaging--api-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/imaging-api)
[![trust-api](https://img.shields.io/badge/docker-trust--api-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/trust-api)

[![orthanc](https://img.shields.io/badge/docker-orthanc-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/orthanc)
[![xnat-db](https://img.shields.io/badge/docker-xnat--db-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/xnat-db)
[![xnat-nginx](https://img.shields.io/badge/docker-xnat--nginx-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/xnat-nginx)
[![xnat-web](https://img.shields.io/badge/docker-xnat--web-blue?logo=docker)](https://github.com/londonaicentre/FLIP/pkgs/container/xnat-web)

FLIP is an open-source platform for federated training and evaluation of medical imaging AI models across healthcare institutions, while ensuring data privacy and security.

FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) in collaboration with Guy's and St Thomas' NHS Foundation Trust and King's College London.

<p align="center"><img src="docs/source/assets/flip-diagram-2-nodes.png" alt='flip-architecture' /></p>

## Repositories

This repository is the FLIP mono-repo: Central Hub API, Trust APIs, UI, Docker deployment, **and** the federated
learning code (base library, FL services, and tutorials) that was previously split across `flip-fl-base` and
`flip-fl-base-flower`. The FL code now lives under [`flip-utils/`](flip-utils/) (the pip-installable `flip` package),
[`fl_services/`](fl_services/) (Docker services for FL server/client/API), and [`fl-apps/`](fl-apps/) (job-type
implementations and tutorials).

| Subdirectory | Description |
| --- | --- |
| [`flip-api/`](flip-api/) | Central Hub API service |
| [`flip-ui/`](flip-ui/) | Frontend UI |
| [`trust/`](trust/) | Trust-side services (trust-api, imaging-api, data-access-api, mock OMOP / Orthanc / XNAT) |
| [`deploy/`](deploy/) | Docker Compose and infrastructure-as-code (AWS / on-prem) |
| [`docs/`](docs/) | Sphinx documentation source (ReadTheDocs) |
| [`flip-utils/`](flip-utils/) | `flip` Python package — platform logic, NVFLARE components, Flower helpers |
| [`fl_services/`](fl_services/) | Docker images for FL networks: `fl-server`, `fl-client`, `fl-api-base`, `fl-base` |
| [`fl-apps/`](fl-apps/) | FL job-type implementations (`standard`, `evaluation`, `diffusion_model`, `fed_opt`) and tutorials |

The legacy [`flip-fl-base`](https://github.com/londonaicentre/flip-fl-base) and
[`flip-fl-base-flower`](https://github.com/londonaicentre/flip-fl-base-flower) repositories still hold the
provisioned NVFLARE workspaces and Flower certs used at dev time — see
[Federated Learning Setup](#federated-learning-setup) below.

## Deployment

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with [Swarm mode](https://docs.docker.com/engine/swarm/) initialized
- [Nvidia Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [Make](https://formulae.brew.sh/formula/make)
- [UV](https://docs.astral.sh/uv) - Python environment management tool
- postgresql-client (install with `apt install postgresql-client postgresql-client-common` on Debian/Ubuntu)

> For developer tooling and IDE setup, see [CONTRIBUTING.md](CONTRIBUTING.md).

### Using the Makefile

To start the services, you can use the Makefile provided in the root directory. The Makefile provides several convenient commands to manage the services defined in the [deploy/compose.development.yml](deploy/compose.development.yml) file.

For example:

| Command | Description |
| --------- | ------------- |
| `make up` | Run all services using Docker Swarm for XNAT (⚠️ This will not build the images, use `make build` first if needed)|
| `make up-no-trust` | Run all services except the trust services related services |
| `make up-trusts` | Run the trust services related services (uses Docker Swarm for XNAT) |
| `make central-hub` | Run the central API service and the database (does not start the UI — use `make ui` for that) |
| `make ui` | Start the flip-ui container only (no-op when `PROD=stag`/`PROD=true`, since the UI is served from S3 + CloudFront) |
| `make ui-off` | Stop the flip-ui container |
| `make build` | Build all Docker images |
| `make down` | Stop all services and remove the containers (including Swarm stacks) |
| `make restart` | Stop and start all services |
| `make restart-no-trust` | Stop and start all services except the trust services related services |
| `make clean` | Remove all stopped containers, networks, and images |
| `make ci` | Run the CI pipeline locally using `act` |
| `make -C trust up-trust KIT=<CODE> PROD=<env>` | Run a trust on the local host pointing at a remote hub (kit file `trust/.env.<CODE>.<env>`; the on-prem trust kit is `trust/.env.<CODE>.production`) |
| `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."` | Scaffold a new trust kit file `trust/.env.<CODE>.<env>` from the base template |
| `make register-trusts` | Register the shipped dev roster (`trust/.env.*.development.example`, currently GSTT + KCH) on the running hub and write per-trust kit files (run automatically by `make up`) |
| `make register-trust KIT=<CODE>` | Register one trust on the running hub and fill its kit file (creds + hub-shared block) |
| `make unit_test` | Run unit tests across all services |
| `make tests` | Run flip-ui unit and e2e tests followed by the full flip-api test suite (lint + mypy + pytest) |
| `make e2e_smoke` | Drive a full project lifecycle (create → upload → train → download) against an already-running stack (not run in CI) |
| `make lock` | Regenerate every service's `uv.lock` from its `pyproject.toml` |
| `make debug SERVICE=<name>` | Restart one service in debug mode (waits for a debugger on port 5678). Services: `flip-api`, `fl-api-net-1`, `trust-api`, `imaging-api`, `data-access-api` |
| `make debug-off SERVICE=<name>` | Take a single service back out of debug mode |
| `make debug-all` | Restart every API service in debug mode |
| `make debug-off-all` | Take every API service back out of debug mode |

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
- The Swarm deployment uses the [trust/xnat/docker-compose-stack.yml](trust/xnat/docker-compose-stack.yml) file
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

To manually manage XNAT services (uses Docker Swarm):

```bash
cd trust/xnat
make up          # Start XNAT services
make down        # Stop XNAT services
make xnat-shell  # Get a shell in the XNAT container
```

### Trust Registration

The internal service key (for fl-server-to-hub authentication) must be generated before starting the platform:

```bash
make generate-internal-service-key
```

This writes `INTERNAL_SERVICE_KEY` and `INTERNAL_SERVICE_KEY_HASH` (plain strings) into `.env.development`. `make up` invokes `generate-internal-service-key` automatically.

Trusts are registered on the **running hub** rather than configured via env-file key dicts:

```bash
make register-trusts
```

`register-trusts` registers the shipped dev roster — every `trust/.env.*.development.example` kit (currently GSTT and KCH) — on the hub: for each, it inserts a `trust` row with its `api_key_hash`, claims an FL kit slot, and fills that trust's kit file `trust/.env.<CODE>.development` (with `TRUST_API_KEY`, `TRUST_INTERNAL_SERVICE_KEY`, `FL_KIT_SLOT`, `FL_KIT_SLOT_NUMBER`, `EXPECTED_TRUST_ID`). The kit files ARE the roster — trusts are not enumerated in the hub env file. To add another, run `make new-trust TRUST_CODE=<CODE> TRUST_NAME="..."` then `make register-trust KIT=<CODE>`. `make up` runs `register-trusts` automatically once the hub is up. See [`CLAUDE.md`](CLAUDE.md#trust-internal-service-authentication) for the trust-internal auth threat model.

### Basic Usage

To start the full platform locally:

```bash
make up
```

This will start all the services defined in the `deploy/compose.development.yml` file. The services will be started in detached
mode, so you can continue using your terminal. Use `docker compose ps` to see the status of the services and see which
ports they are running on.

To get a shell some of the services, you can run:

```bash
docker compose -f deploy/compose.development.yml exec < service-name > < command >
```

For example:

```bash
docker compose -f deploy/compose.development.yml exec flip-ui /bin/sh
```

This will give you a shell in the `flip-ui` container. You can run any command inside the container, including
installing new packages, running tests, and debugging the code.

To stop the services:

```bash
make down
```

If you want to run a single service you can run:

```bash
docker compose -f deploy/compose.development.yml run --rm < service name >
```

### Federated Learning Setup

The project supports [NVIDIA FLARE](https://developer.nvidia.com/flare) and [Flower Framework](https://flower.ai/) for
federated learning. FLARE requires provisioned certificates and configuration files. As of the FL-code migration these
can be generated from within this repo (`make nvflare-provision-2-nets`, see [`fl_services/README.md`](fl_services/README.md)),
but until the deploy-side path migration lands the dev compose files still consume the legacy sibling-repo workspaces.

1. **Path Resolution**: `FL_PROVISIONED_DIR` is derived from the `FL_BACKEND` selection inside [`deploy/fl_backend.mk`](deploy/fl_backend.mk) (no longer set in `.env.development`):

   - `FL_BACKEND=flower` → `../flip-fl-base-flower/certs`
   - `FL_BACKEND=nvflare` → `../flip-fl-base/workspace`

   The Makefile then converts the relative value to an absolute path so Docker volume mounts work correctly. You can override the resolved path at the command line for a one-off, e.g. `make up FL_PROVISIONED_DIR=/tmp/my-workspace`.

2. **Why This Matters**: Docker Compose cannot resolve relative paths for volume mounts, so the absolute path conversion is essential for FL services to access their provisioned certificates and configuration files.

If you see errors like "fed_client.json does not exist" or "missing startup folder", verify that:

- Either the [flip-fl-base](https://github.com/londonaicentre/flip-fl-base) repository is cloned as a sibling directory
  with a provisioned workspace, **or** override `FL_PROVISIONED_DIR` to point at the workspace you generated from
  `make nvflare-provision-2-nets`
- The workspace has been properly provisioned with NVFLARE certificates
- The `FL_PROVISIONED_DIR` path is correctly resolved (check Makefile output)

## AWS Deployment

For production deployments on AWS, see the [AWS Deployment Guide](deploy/README.md). This covers provisioning
infrastructure with OpenTofu (Terraform), configuring AWS services, and deploying the platform at scale.

For hybrid on-premises trust deployments, see the [On-Prem Trust Deployment Guide](deploy/providers/local/README.md).

## Project Structure

The repository is organised as follows:

- `deploy`: Contains the Docker deployment and infrastructure files
- `docs`: Contains the documentation files
- `flip-api`: Contains the central hub API service
- `flip-ui`: Contains the UI service
- `trust`: Contains the services that would be deployed in individual trust environments.
  - `data-access-api`: Contains the data access API service
  - `imaging-api`: Contains the imaging API service
  - `observability`: Contains the observability stack (Grafana, Loki, Alloy)
  - `omop-db`: Contains a mocked OMOP database
  - `orthanc`: Contains a mocked PACS service (uses [Orthanc](https://www.orthanc-server.com/))
  - `trust-api`: Contains the trust API service
  - `xnat`: Contains a mocked [XNAT](https://www.xnat.org/) service
- `flip-utils`: The `flip` Python package — platform logic, NVFLARE components, Flower helpers (migrated from `flip-fl-base`)
- `fl_services`: Docker images for FL networks — `fl-server`, `fl-client`, `fl-api-base`, `fl-base` (migrated from `flip-fl-base`)
- `fl-apps`: FL job-type implementations (`standard`, `evaluation`, `diffusion_model`, `fed_opt`) and runnable tutorials

### Trust Authentication

Trusts authenticate to the Central Hub using per-trust API keys. All trust communication is outbound — trusts poll the hub over HTTPS at the canonical subdomain (e.g. `https://app.flip.aicentre.co.uk/api/...`), which is fronted by CloudFront and proxied to the ALB. See [CLAUDE.md](CLAUDE.md) for the full authentication model and [deploy/providers/local/README.md](deploy/providers/local/README.md) for on-premises trust deployment.

## Contributing

We welcome contributions from the community. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidance on setting up a
development environment, adding new services, coding standards, testing practices, and the pull request process.

## Further Resources

- [Full Documentation](https://londonaicentreflip.readthedocs.io/en/latest/)
- [AWS Deployment Guide](deploy/README.md)
- [Debugging Guide](DEBUG.md)
- [Security & Secrets](scripts/README.md)
- [London AI Centre](https://www.aicentre.co.uk/)
