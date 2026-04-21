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

# FLIP-FL-base-FLARE

<p align="left">
<img src="assets/flip-flare-logo.png" height="200" alt='flip-flare-logo' />
</p>

[![codecov](https://codecov.io/gh/londonaicentre/flip-fl-base/graph/badge.svg)](https://codecov.io/gh/londonaicentre/flip-fl-base)
[![PyPI version](https://img.shields.io/pypi/v/flip-utils)](https://pypi.org/project/flip-utils/)
[![Docker - flare-fl-base](https://img.shields.io/badge/docker-flare--fl--base-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-base)
[![Docker - flare-fl-server](https://img.shields.io/badge/docker-flare--fl--server-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-server)
[![Docker - flare-fl-client](https://img.shields.io/badge/docker-flare--fl--client-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-client)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](./LICENSE.md)

This repository contains the [FLIP](https://github.com/londonaicentre/FLIP) (Federated Learning and Interoperability
Platform) federated learning base application utilities. It is a monorepo that includes:

- **[`flip`](./flip/)** — pip-installable Python package with platform logic, NVFLARE components, and utilities
- **[`tutorials/`](./tutorials/)** — example applications you can run on the FLIP platform
- **[`fl_services/`](./fl_services/)** — Docker services for running FL networks (server, clients, admin API)

## Table of Contents

- [flip Python Package](#flip-python-package)
  - [Installation](#installation)
  - [Package Structure](#package-structure)
  - [User Application Requirements](#user-application-requirements)
  - [Job Types](#job-types)
  - [Development Mode](#development-mode)
  - [Unit Tests](#unit-tests)
- [Tutorials](#tutorials)
  - [App / Tutorial Compatibility](#app--tutorial-compatibility)
- [FL Services API](#fl-services-api)
  - [Prerequisites](#prerequisites)
  - [Provisioning a Network](#provisioning-a-network)
  - [Running the Network](#running-the-network)
  - [Integration Testing](#integration-testing)
  - [CI/CD](#cicd)
  - [Makefile Reference](#makefile-reference)
- [Security](#security)
- [Contributing](#contributing)

---

## flip Python Package

The [`flip`](./flip/) package is the core pip-installable library for the FLIP federated learning platform. It provides
all platform logic core to training and evaluating FL applications.

### Installation

```bash
uv sync
# or
pip install .
```

To build a distributable package:

```bash
uv build
```

### Package Structure

```text
flip/
├── core/         # FLIPBase, FLIPStandardProd/Dev implementations, FLIP() factory
├── constants/    # FlipConstants (pydantic-settings), enums, PTConstants
├── utils/        # General utilities: Utils, model weight helpers
├── nvflare/      # NVFLARE-specific logic and components
│   ├── executors/    # RUN_TRAINER, RUN_VALIDATOR, RUN_EVALUATOR wrappers
│   ├── controllers/  # Workflow controllers (ScatterAndGather, CrossSiteModelEval, …)
│   └── components/   # Event handlers, persistors, privacy filters, locators, …
└── flower/       # Flower-specific server-side helpers
    └── metrics.py    # handle_client_metrics / handle_client_exception
```

The `FLIP()` factory selects `FLIPStandardDev` (local CSV/filesystem) or `FLIPStandardProd` (FLIP platform APIs) based
on the `LOCAL_DEV` environment variable.

The `flip.flower` sub-package is intended **only for fl-server code**. Its helpers forward per-client metrics and
crashed-reply exceptions — extracted from Flower reply Messages in `Strategy.aggregate_train` /
`aggregate_evaluate` — to the Central Hub. fl-client containers must never import it and must never hold the
`INTERNAL_SERVICE_KEY` credential. For the NVFLARE equivalent, see `flip.nvflare.metrics`.

### User Application Requirements

User-provided files go in the job's `custom/` directory and are dynamically imported by the executor wrappers:

| File | Description |
| ------ | ------------- |
| `trainer.py` | Training logic — must export `FLIP_TRAINER` class |
| `validator.py` | Validation logic — must export `FLIP_VALIDATOR` class |
| `models.py` | Model definitions — must export `get_model()` function |
| `config.json` | Hyperparameters — must include `LOCAL_ROUNDS` and `LEARNING_RATE` |
| `transforms.py` | Data transforms (optional) |

### Job Types

Set via the `JOB_TYPE` environment variable:

| Type | Description |
| ------ | ------------- |
| `standard` | Federated training with FedAvg aggregation (default) |
| `evaluation` | Distributed model evaluation without training |
| `diffusion_model` | Two-stage training (VAE encoder + diffusion) |
| `fed_opt` | Custom federated optimization |

The corresponding configs live in `src/<job_type>/app/config/`.

### Development Mode

DEV mode lets you test FL applications locally before deploying to production.

1. Edit `.env.development`:

   ```bash
   LOCAL_DEV=true
   DEV_IMAGES_DIR=../data/accession-resources
   DEV_DATAFRAME=../data/sample_get_dataframe.csv
   JOB_TYPE=standard
   ```

2. Place your application files in `src/<JOB_TYPE>/app/custom/`.

3. Run the simulator in Docker:

   ```bash
   make run-container
   ```

### Unit Tests

```bash
make unit-test
# or
uv run pytest -s -vv
```

---

## Tutorials

The [`tutorials/`](./tutorials/) directory contains ready-to-use example applications that can be uploaded to the FLIP platform UI. Each tutorial is designed to work with a specific app type from `src/`.

![FL app structure](./assets/fl_app_structure.png)

### App / Tutorial Compatibility

| App | Tutorial |
|-----|----------|
| `standard` | `image_segmentation/3d_spleen_segmentation` |
| `standard` | `image_classification/xray_classification` |
| `diffusion_model` | `image_synthesis/latent_diffusion_model` |
| `fed_opt` | `image_segmentation/3d_spleen_segmentation` |
| `evaluation` | `image_evaluation/3d_spleen_segmentation` |

---

## FL Services API

The [`fl_services/`](./fl_services/README.md) directory contains Docker-based NVFLARE services. See the [FL services README](./fl_services/README.md) and the [FL API README](./fl_services/fl-api-base/README.md) for full details on provisioning and the API endpoints.

### Prerequisites

- Docker and Docker Compose
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- AWS CLI configured (for downloading test data)

### Provisioning the 2 Networks

Generate the certificates, keys, and configuration for the 2 FL networks:

```bash
make nvflare-provision-2-nets
```

This uses the network-specific provisioning project files (`net-1_project.yml` and `net-2_project.yml`) and provisions the network files in `workspace/net-1` and `workspace/net-2` (gitignored) using the [scripts/provision-network.sh](scripts/provision-network.sh) script.

> ⚠️ **Warning**: Provisioned files contain cryptographic signatures. Any modification will cause errors. Always re-run provisioning if changes are needed.

### Provisioning Networks for Staging/Production

Note the provisioning project file `net-1_project_stag.yml` changes the name of the FL server to the full domain name i.e. `stag.flip.aicentre.co.uk` instead of `fl-server-net-1`, since the FL
clients won't be on the same Docker network as the FL server (as they are in development) and won't be able to resolve internal Docker hostnames.

Run:

```bash
make nvflare-provision-stag
```

### Creating a New Network

Create a provisioning project file (e.g. `net-3_project.yml`) based on the template (`net-1_project.yml`) (you'll likely need to change `fed_learn_port`) and run:

```bash
NET_NUMBER=3 make nvflare-provision
```

### Running a Network

```bash
make build NET_NUMBER=1   # Build Docker images
make up NET_NUMBER=1      # Start the network (server, 2 clients, API)
make down NET_NUMBER=1    # Stop the network
make clean NET_NUMBER=1   # Remove containers and images
```

### Integration Testing

Download test data (requires AWS S3 access) then run the relevant target:

```bash
make download-test-data
make test   # Run all integration tests
```

### CI/CD

GitHub Actions workflows use OIDC to authenticate to AWS (no long-lived keys).

| Trigger | Target |
| --------- | -------- |
| PR to any branch | `s3://flipdev/base-application-dev/nvflare/pull-requests/<PR_NUMBER>` |
| Merge to `develop` | `s3://flipdev/base-application/nvflare` and `s3://flipstag/base-application/nvflare` |
| Merge to `main` | `s3://flipprod/base-application/nvflare` |

> **Warning**: Never manually sync to the production bucket.

To test a PR on the FLIP platform, update `FL_APP_BASE_BUCKET` in the [flip repo environment variables](https://github.com/londonaicentre/FLIP/blob/main/.env.development) to point to your PR's S3 path.

### Makefile Reference

#### Network Management

| Command | Description |
| --------- | ------------- |
| `make nvflare-provision NET_NUMBER=X` | Provision FL network X |
| `make build NET_NUMBER=X` | Build Docker images for network X |
| `make up NET_NUMBER=X` | Start FL network X |
| `make down NET_NUMBER=X` | Stop FL network X |
| `make clean NET_NUMBER=X` | Remove containers and images |

#### Development

| Command | Description |
| --------- | ------------- |
| `make run-container` | Run NVFLARE simulator in Docker |

#### Data Management

| Command | Description |
| --------- | ------------- |
| `make download-test-data` | Download all test data (x-ray and spleen images, model checkpoints) from S3 |

#### Testing

| Command | Description |
| --------- | ------------- |
| `make unit-test` | Run pytest unit tests for flip python package |
| `make test-xrays-standard` | Test standard job with x-ray data |
| `make test-spleen-standard` | Test standard job with spleen data |
| `make test-spleen-evaluation` | Test evaluation job with spleen data |
| `make test-spleen-diffusion` | Test diffusion model with spleen data |
| `make test` | Run all integration tests |

---

## Security

Please report security vulnerabilities responsibly. For details on how to report a vulnerability, see [SECURITY.md](./SECURITY.md).

**⚠️ Do not open a public GitHub issue for security bugs; instead, use the private GitHub Security Advisory feature.**

---

## Contributing

For information on how to contribute to this project, see [CONTRIBUTING.md](./CONTRIBUTING.md).
