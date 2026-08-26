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

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/source/assets/flip-logo-text-dark.png">
    <img src="docs/source/assets/flip-logo-text.png" height="200" alt="FLIP">
  </picture>
</p>

# Federated Learning Interoperability Platform

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg)](https://codecov.io/gh/londonaicentre/FLIP)

FLIP is an open-source platform for federated training and evaluation of medical-imaging AI models across healthcare
institutions. Models travel to the data held inside each institution; patient data remains within the institution's
security boundary.

The platform combines a Central Hub for project orchestration with independently operated Trust nodes. It supports
both [NVIDIA FLARE](https://nvflare.readthedocs.io/) and [Flower](https://flower.ai/docs/) as federated-learning
backends. FLIP is developed by the [London AI Centre](https://www.aicentre.co.uk/) with Guy's and St Thomas' NHS
Foundation Trust and King's College London.

For the platform architecture, workflows, deployment guides, and user documentation, start with the
[FLIP documentation](https://londonaicentreflip.readthedocs.io/en/latest/).

## Quickstart: Central Hub with two example Trusts

This developer quickstart starts the Central Hub and the shipped GSTT and KCH example Trust nodes on one Linux host.
It uses the development AWS resources and XNAT artifacts maintained for authorised FLIP developers. If you do not
have access to those resources, begin with the
[Central Hub deployment guide](https://londonaicentreflip.readthedocs.io/en/latest/deploy-flip/deploy-central-hub.html) to create your
own environment.

### Prerequisites

- Docker Engine with Compose and Swarm mode, plus the NVIDIA Container Toolkit on GPU hosts
- GNU Make, `jq`, the AWS CLI, and [uv](https://docs.astral.sh/uv/)
- An AWS SSO profile with access to the development Cognito, S3, and SES resources
- GitHub Container Registry access for the published FLIP images

The complete tool list and environment-variable checklist are in [CONTRIBUTING.md](CONTRIBUTING.md#prerequisites).

### Start the platform

```bash
cp .env.development.example .env.development
# Fill the required AWS, Cognito, SES, database, encryption, and S3 values.

aws sso login --profile <your-profile>
docker login ghcr.io

# Required once per Docker host.
docker swarm init

# Provision the two local NVFLARE networks used by the example Trusts. `make up` does not do
# this for you, and the FL containers cannot start without it.
make -C fl-services/nvflare provision-2-nets

# Pull the published service images, start the hub, register GSTT and KCH,
# then start both Trust and XNAT stacks.
make up
```

If Swarm is already active, `docker swarm init` reports that and can be skipped. Open `https://localhost` for the UI
and `http://localhost:8080/docs` for the Central Hub API documentation.

### Load the OMOP vocabulary

`make up` fetches each Trust's OMOP data automatically, but the published tarballs are **vocab-free**. Load the core
vocabulary once per Trust, then restart the readers so they drop their cached query results:

```bash
make -C trust/omop-db load-omop-vocab                    # Trust_1 (GSTT, port 5434)
make -C trust/omop-db load-omop-vocab OMOP_DB_PORT=5436  # Trust_2 (KCH)

docker restart trust1-data-access-api-1 trust2-data-access-api-1
```

Skip it and every cohort query returns zero rows, which surfaces several steps later as a project that cannot be
staged — `returned no cohort records (zero or privacy-suppressed)` — so it reads as a disclosure-threshold problem
rather than a missing vocabulary. This is the one step needing credentials for the AI Centre's bundle; contributors
without that access self-serve from OHDSI Athena. See
[`trust/omop-db/README.md`](trust/omop-db/README.md#the-core-vocabulary-bundle) for the bundle and both routes.

To run the scripted project lifecycle against the running stack:

```bash
make e2e_smoke
```

This creates a project, submits a cohort query, waits for imaging import, runs federated training, and downloads the
result. It is intentionally not part of CI and can take several minutes.

Stop the local platform with:

```bash
make down
```

The default backend is NVFLARE. To run the same topology with Flower, provision its per-net credentials instead —
once per network, and again before `make up`:

```bash
make -C fl-services/flower provision NET_NUMBER=1
make -C fl-services/flower provision NET_NUMBER=2
make up FL_BACKEND=flower
```

See the [Flower service guide](fl-services/flower/README.md) for the full workflow. Use `make up BUILD=true` when
dependency or Dockerfile changes require locally rebuilt images; ordinary source edits are bind-mounted for live
reload. More detail is in [Running the stack](CONTRIBUTING.md#running-the-stack-pull-vs-build).

## Where to go next

| Goal | Guide |
| --- | --- |
| Understand the platform and its security model | [ReadTheDocs](https://londonaicentreflip.readthedocs.io/en/latest/) |
| Set up a development environment or contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Run or adapt a federated-learning example | [FL tutorials](fl-tutorials/README.md) |
| Build a FLIP application | [Working with FLIP apps](https://londonaicentreflip.readthedocs.io/en/latest/working-with-flip-apps.html) |
| Deploy the Central Hub on AWS | [Central Hub deployment](docs/source/deploy-flip/deploy-central-hub.rst) |
| Deploy a Trust on premises | [Local provider](deploy/providers/local/README.md) |
| Deploy a Trust on Kubernetes | [Kubernetes provider](deploy/providers/kubernetes/README.md) |
| Operate Trust-side services | [Trust services](trust/README.md) |
| Debug a service in VS Code | [DEBUG.md](DEBUG.md) |
| Debug or test a particular service | That service's README and Makefile |

## Repository layout

FLIP is maintained as one monorepo. Each major area owns its detailed setup and operational documentation.

| Directory | Responsibility |
| --- | --- |
| [`flip-api/`](flip-api/) | Central Hub FastAPI service, database, scheduling, and project lifecycle |
| [`flip-ui/`](flip-ui/) | Vue 3 web application |
| [`trust/`](trust/) | Trust gateway, data and imaging APIs, and local OMOP/PACS/XNAT services |
| [`flip-utils/`](flip-utils/) | Shared, pip-installable `flip` Python library |
| [`fl-services/`](fl-services/) | NVFLARE and Flower network services, images, and provisioning |
| [`fl-apps/`](fl-apps/) | Backend-specific application templates bundled by the Central Hub |
| [`fl-tutorials/`](fl-tutorials/) | Worked federated-learning applications and local runners |
| [`deploy/`](deploy/) | Compose configuration and AWS, on-premises, and Kubernetes providers |
| [`docs/`](docs/) | Sphinx source published on ReadTheDocs |
| [`scripts/`](scripts/) | Repository-wide development and deployment helpers |

## Contributing and support

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request; commits must
include a [DCO sign-off](https://developercertificate.org/). Use
[GitHub Issues](https://github.com/londonaicentre/FLIP/issues) for bugs, feature proposals, and documentation gaps.

For security concerns, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

FLIP is licensed under the [Apache License 2.0](LICENSE.md).
