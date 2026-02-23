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

# flip-fl-base

[![codecov](https://codecov.io/gh/londonaicentre/flip-fl-base/graph/badge.svg)](https://codecov.io/gh/londonaicentre/flip-fl-base)
[![PyPI version](https://img.shields.io/pypi/v/flip-utils)](https://pypi.org/project/flip-utils/)
[![Docker - flare-fl-base](https://img.shields.io/badge/docker-flare--fl--base-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-base)
[![Docker - flare-fl-server](https://img.shields.io/badge/docker-flare--fl--server-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-server)
[![Docker - flare-fl-client](https://img.shields.io/badge/docker-flare--fl--client-blue?logo=docker)](https://github.com/londonaicentre/flip-fl-base/pkgs/container/flare-fl-client)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Documentation](https://img.shields.io/badge/docs-README-informational)](https://github.com/londonaicentre/flip-fl-base#readme)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](./LICENSE.md)

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
└── nvflare/      # NVFLARE-specific logic and components
    ├── executors/    # RUN_TRAINER, RUN_VALIDATOR, RUN_EVALUATOR wrappers
    ├── controllers/  # Workflow controllers (ScatterAndGather, CrossSiteModelEval, …)
    └── components/   # Event handlers, persistors, privacy filters, locators, …
```

The `FLIP()` factory selects `FLIPStandardDev` (local CSV/filesystem) or `FLIPStandardProd` (FLIP platform APIs) based
on the `LOCAL_DEV` environment variable.

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

### Provisioning a Network

Generate the certificates, keys, and configuration for a new FL network:

```bash
make nvflare-provision NET_NUMBER=1
```

This creates a network-specific compose file (`deploy/compose-net-1.yml`) and service secrets in `workspace/net-1/services/` (gitignored). Multiple networks can be provisioned with different ports:

```bash
make nvflare-provision NET_NUMBER=2 FL_PORT=8004 ADMIN_PORT=8005
```

> **Warning**: Provisioned files contain cryptographic signatures. Any modification will cause errors. Always re-run provisioning if changes are needed.

### Running the Network

```bash
make build NET_NUMBER=1   # Build Docker images
make up NET_NUMBER=1      # Start the network (server, 2 clients, API)
make down NET_NUMBER=1    # Stop the network
make clean NET_NUMBER=1   # Remove containers and images
```

### Integration Testing

Download test data (requires AWS S3 access) then run the relevant target:

```bash
make download-xrays-data && make test-xrays-standard
make download-spleen-data && make test-spleen-standard
make download-checkpoints && make test-spleen-evaluation
make test-spleen-diffusion
make test   # Run all integration tests
```

### CI/CD

GitHub Actions workflows use OIDC to authenticate to AWS (no long-lived keys).

| Trigger | Target |
| --------- | -------- |
| PR to any branch | `s3://flipdev/base-application-dev/pull-requests/<PR_NUMBER>/src/` |
| Merge to `develop` | `s3://flipdev/base-application-dev/src/` and `s3://flipstag/base-application/src/` |
| Merge to `main` | `s3://flipprod/base-application/src/` |

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

#### Testing

| Command | Description |
| --------- | ------------- |
| `make unit-test` | Run pytest unit tests |
| `make test-xrays-standard` | Test standard job with x-ray data |
| `make test-spleen-standard` | Test standard job with spleen data |
| `make test-spleen-evaluation` | Test evaluation job with spleen data |
| `make test-spleen-diffusion` | Test diffusion model with spleen data |
| `make test` | Run all integration tests |

#### Data Management

| Command | Description |
| --------- | ------------- |
| `make download-xrays-data` | Download x-ray test images from S3 |
| `make download-spleen-data` | Download spleen test images from S3 |
| `make download-checkpoints` | Download model checkpoints from S3 |
| `make copy-spleen-app` | Copy test app to dev folder |
| `make save-spleen-app` | Save dev changes to test folder |
| `make pull-spleen-app` | Pull latest app from tutorials repo |
