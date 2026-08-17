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

# flip-utils — the `flip` Python package

[![PyPI version](https://img.shields.io/pypi/v/flip-utils)](https://pypi.org/project/flip-utils/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Documentation Status](https://readthedocs.org/projects/londonaicentreflip/badge/?version=latest)](https://londonaicentreflip.readthedocs.io/en/latest/)[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](../LICENSE.md)

This directory is a sub-tree of the [FLIP](https://github.com/londonaicentre/FLIP) mono-repo. It hosts the
pip-installable `flip` Python package (published as `flip-utils` on PyPI) that ships inside every FL server / client
image and is imported as `from flip import ...` by user-uploaded training code. Sibling FL trees in the same mono-repo:

- **[`flip-utils/flip/`](./flip/)** — pip-installable Python package with platform logic, NVFLARE components, Flower helpers and utilities (this directory)
- **[`../fl-apps/`](../fl-apps/)** — FL job-type implementations / app templates per backend (`nvflare/{standard,evaluation,diffusion_model,fed_opt}`, `flower/{standard,evaluation}`)
- **[`../fl-tutorials/`](../fl-tutorials/)** — runnable end-to-end tutorial examples per backend (`nvflare/`, `flower/`)
- **[`../fl-services/`](../fl-services/)** — Docker images and network provisioning for FL networks per backend (`nvflare/`, `flower/`); each backend's `Makefile` owns build / provision / up / down / submit

Paths like `tutorials/` referenced in older sections of this README refer to the now-sibling top-level
[`../fl-tutorials/`](../fl-tutorials/) tree, and Make targets called out below run from the `flip-utils/` directory
unless otherwise noted.

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
│   ├── controllers/  # FLIP workflows (ScatterAndGather, BroadcastTask, …)
│   ├── components/   # Event handlers, persistors, privacy filters, locators, …
│   ├── recipes/      # High-level NVFLARE job recipes
│   ├── runtime.py    # Runtime helpers for NVFLARE apps
│   └── metrics.py    # Metrics collection and reporting
└── flower/       # Flower-specific server-side helpers
    ├── metrics.py    # handle_client_metrics / handle_client_exception
    ├── progress.py   # RoundTelemetry + typed round events
    ├── selection.py  # BestModelSelector + best-model run-config parsing
    └── strategy.py   # FlipFedAvg (hub telemetry + best-model wiring; needs flwr)
```

The `FLIP()` factory selects `FLIPStandardDev` (local CSV/filesystem) or `FLIPStandardProd` (FLIP platform APIs) based
on the `LOCAL_DEV` environment variable.

The `flip.flower` sub-package is intended **only for fl-server code**. Its helpers forward per-client metrics and
crashed-reply exceptions — extracted from Flower reply Messages in `Strategy.aggregate_train` /
`aggregate_evaluate` — to the Central Hub. fl-client containers must never import it and must never hold the
`INTERNAL_SERVICE_KEY` credential. For the NVFLARE equivalent, see `flip.nvflare.metrics`.

`FlipFedAvg` also owns opt-in **best-global-model selection**: constructed with `best_model_metric` (and
`best_model_metric_minimize` for loss-like metrics), it scores each round's freshly aggregated model on the
aggregated evaluation metrics (`flip.flower.selection.BestModelSelector`) and retains the best-scoring arrays.
The `fl-apps/flower/standard` template maps the `best-model-metric` / `best-model-metric-minimize` run-config
keys onto it and saves `best_FL_global_model.pt` alongside `FL_global_model.pt` when a selection actually
happened — unset, evaluation stays final-round-only and no best artefact is produced. NVFLARE reaches the same
contract with the stock `IntimeModelSelector` (see `FlipFedAvgRecipe`).

**The two backends aggregate the selection metric differently, deliberately.** On Flower the value the
selector sees is whatever stock `FedAvg` produced — a **weighted average keyed on `num-examples`**, so a
trust with more test samples pulls the score further. NVFLARE's `IntimeModelSelector` defaults to
`weigh_by_local_iter=False` with no `aggregation_weights`, i.e. a plain **unweighted mean** across clients.
Neither lets a single site dictate selection, but the same metric can pick a different round on each backend
when trust cohort sizes are uneven; expect that rather than treating it as a bug.

### User Application Requirements

The job components dynamically import user-provided files from the job's `custom/`
directory. On the platform that directory is assembled by the FL API, which merges the
uploaded app files onto the matching `fl-apps/nvflare/<JOB_TYPE>/app` template; in local
SimEnv runs the tutorial's `job.py` stages its `app_files/` into the job's `custom/`
directly. The `diffusion_model` template additionally carries a git-tracked `custom/`
with baseline files that the merge extends.

| File | Description |
| ------ | ------------- |
| `trainer.py` | Training logic — a plain `nvflare.client` script (`flare.init`/`receive`/`send`) |
| `validator.py` | Extra validation module where the job type requires one (`diffusion_model`) |
| `models.py` | Model definitions — must export `get_model()` function |
| `config.json` | Hyperparameters — must include `LOCAL_ROUNDS` and `LEARNING_RATE`; optional `BEST_MODEL_METRIC` / `BEST_MODEL_METRIC_MINIMIZE` enable best-model selection (see below) |
| `transforms.py` | Data transforms (optional) |

### Best-Model Selection (optional)

By default only the final aggregated model is saved. Setting `BEST_MODEL_METRIC` (and, for
loss-like metrics, `BEST_MODEL_METRIC_MINIMIZE: true`) wires NVFLARE's stock
`IntimeModelSelector` into the job so the best global model is saved too:

- Each round, clients evaluate the **received global model** on their local validation split
  *before* training and report the metrics via `FLModel(metrics={...})` — so the selection metric
  measures exactly the checkpoint being considered.
- The selector averages the chosen metric across clients and, on improvement, has the persistor
  save `best_FL_global_model.pt` next to `FL_global_model.pt` — same file format as the final
  model. Round 0 is skipped (no aggregated model exists yet), so selection needs
  `GLOBAL_ROUNDS >= 2` — a single-round job can never save a best model.
- The **final model itself is never a selection candidate**: metrics are evaluated on received
  global models before training, and the last round's aggregate is never re-broadcast, so there
  is no post-last-round evaluation. "Best" therefore means best among the intermediate global
  models — the final model may in fact outperform the saved best, in which case the two files
  differ even though the final is the better checkpoint.
- The results zip contains the best model only when selection ran and saved one; no best file is
  fabricated from the final model otherwise.

The metric label must be one the trainer reports (e.g. the client-API x-ray tutorial reports
`VAL_LOSS`, per-lesion `VAL-<METRIC>-<lesion>` and macro `VAL-<METRIC>` labels). Wired for both
supported paths: the `standard` recipe (`FlipFedAvgRecipe(best_model_metric=...)`) and
platform-submitted jobs, where fl-api-base's job-assembly step (`prepare_config.validate_config` /
`configure_server`) reads `BEST_MODEL_METRIC` / `BEST_MODEL_METRIC_MINIMIZE` straight from the
uploaded `config.json` and injects the same selector. On the platform path, a `config.json` that
sets `BEST_MODEL_METRIC` without an explicit `GLOBAL_ROUNDS >= 2` is rejected at upload (the
platform defaults to a single round, where selection could never fire).

### Job Types

Pass the job type to the `FLIP()` factory (`FLIP(job_type=...)`). The `JobType` enum
(`flip.constants.job_types`) defines the values recognised by `FLIP()`:

| Type | Description |
| ------ | ------------- |
| `standard` | Federated training with FedAvg aggregation (default) |
| `evaluation` | Distributed model evaluation without training |
| `diffusion_model` | Two-stage training (VAE encoder + diffusion) |
| `fed_opt` | Custom federated optimization |

The NVFLARE templates under `fl-apps/nvflare/` (`standard`, `evaluation`, `diffusion_model`,
`fed_opt`) are all Client-API apps with recipe-generated configs. The corresponding configs live
in `fl-apps/nvflare/<template>/app/config/`.

### Development Mode

DEV mode lets you run an FL application locally on the NVFLARE simulator before
deploying. The runnable tutorials live in [`../fl-tutorials/`](../fl-tutorials/); each
carries a `.env.app` (`JOB_TYPE`, `DEV_IMAGES_DIR`, `DEV_DATAFRAME`) and a `job.py` that
drives a FLIP recipe on the NVFLARE simulator (SimEnv) from the flip-utils venv.

1. Get the tutorial's dataset. The xray tutorial pulls a reference dataset from Hugging
   Face (`make -C fl-tutorials download-xray-data`); the spleen tutorials generate their
   own data via the segmentation tutorial's `utils/` scripts (see each tutorial's README).

2. Adapt the tutorial's `app_files/` as needed; on the platform they are merged onto the
   matching `fl-apps/nvflare/<JOB_TYPE>/app` template at submit time.

3. Run a tutorial on the simulator (requires a GPU):

   ```bash
   make -C fl-tutorials download-xray-data                       # xray dataset (one-off)
   make -C fl-tutorials list-tutorials
   make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
   make -C fl-tutorials run-all-tutorials   # every tutorial (heavy; stops on first failure)
   ```

### Unit Tests

```bash
make unit-test
# or
uv run pytest -s -vv
```

---

## Tutorials

The [`../fl-tutorials/`](../fl-tutorials/) directory contains ready-to-use example applications that can be uploaded to the FLIP platform UI. Each tutorial is designed to work with a specific app template from `../fl-apps/<backend>/`, and runs on the local FL simulator via `make -C fl-tutorials run-tutorial TUTORIAL=<name>` (defaults to `FL_BACKEND=nvflare`; pass `FL_BACKEND=flower` for Flower).

![FL app structure](./assets/fl_app_structure.png)

### App / Tutorial Compatibility

Paths below are relative to `../fl-tutorials/nvflare/` (the NVFLARE tutorials tree):

| App | Tutorial |
|-----|----------|
| `standard` | `image_classification/xray_classification` |
| `standard` | `image_segmentation/3d_spleen_segmentation` |
| `evaluation` | `image_evaluation/3d_spleen_segmentation_evaluation` |
| `diffusion_model` | `image_synthesis/latent_diffusion_model` |

The legacy Executor job types (`standard`, `evaluation`, `diffusion_model`, `fed_opt`) remain
supported for uploaded apps but no longer have in-tree tutorials — the tutorials above were
migrated to the NVFLARE Client API.

---

## FL Services API

The [`../fl-services/nvflare/`](../fl-services/nvflare/README.md) directory contains Docker-based NVFLARE services. See the [FL services README](../fl-services/nvflare/README.md) and the [FL API README](../fl-services/nvflare/fl-api-base/README.md) for full details on provisioning and the API endpoints.

### Prerequisites

- Docker and Docker Compose
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- AWS CLI configured (for provisioning kit uploads to S3)

### Provisioning the 2 Networks

Generate the certificates, keys, and configuration for the 2 FL networks:

```bash
make -C fl-services/nvflare provision-2-nets
```

This uses the network-specific provisioning project files (`fl-services/nvflare/provision/net-1_project_dev.yml` and `net-2_project_dev.yml`) and provisions the network files in `fl-services/nvflare/provision/workspace-dev/net-1` and `fl-services/nvflare/provision/workspace-dev/net-2` (gitignored) using the [fl-services/nvflare/provision/scripts/provision-network.sh](../fl-services/nvflare/provision/scripts/provision-network.sh) script.

> ⚠️ **Warning**: Provisioned files contain cryptographic signatures. Any modification will cause errors. Always re-run provisioning if changes are needed.

### Provisioning Networks for Staging/Production

Note the provisioning project file `net-1_project_stag.yml` changes the name of the FL server to the full domain name i.e. `stag.flip.aicentre.co.uk` instead of `fl-server-net-1`, since the FL
clients won't be on the same Docker network as the FL server (as they are in development) and won't be able to resolve internal Docker hostnames.

Run:

```bash
make -C fl-services/nvflare provision-stag
```

### Creating a New Network

Create a provisioning project file under `fl-services/nvflare/provision/` (e.g. `net-3_project_dev.yml`) based on the template (`net-1_project_dev.yml`) (you'll likely need to change `fed_learn_port`) and run:

```bash
make -C fl-services/nvflare provision NET_NUMBER=3
```

### Running a Network

From ``fl-services/nvflare/`` (the per-backend Makefile that owns these targets — the
``flip-utils/`` directory itself only ships ``unit-test``):

```bash
make build                # Build the :dev FL images (flare-fl-{base,server,client,api})
make up NET_NUMBER=1      # Start the network (server, 2 clients, API)
make down NET_NUMBER=1    # Stop the network — must match the NET_NUMBER used for `up`
                          # (compose embeds it in container names + mount paths)
```

### Running the tutorials

Each NVFLARE tutorial runs on the local simulator via the [`fl-tutorials/`](../fl-tutorials/)
Makefile. Download the tutorial's dataset first (see its README), then:

```bash
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
make -C fl-tutorials run-all-tutorials
```

### CI/CD

GitHub Actions workflows use OIDC to authenticate to AWS (no long-lived keys).

The base FL application templates (this `fl-apps/` tree) are **no longer published to S3**. They are
baked into the flip-api image at build time and read from a local directory (`FL_APP_BASE_DIR`,
default `/app/fl-apps`); flip-api validates job types and bundles applications straight from that
tree (FLIP#724). Two CI checks still guard the templates on every PR:

- `fl-apps-check-required-files.yml` — regenerates each backend's `required_files.json` from the
  per-template arrays and fails if the committed manifest is stale.
- `fl-apps-check-tutorial-sync.yml` — keeps the Flower tutorials in sync with the `fl-apps/flower/`
  templates.

To exercise a PR's templates on a running FLIP stack **before merge**, rebuild the flip-api image
from the PR branch (the templates ride along in the image) — for local dev the `../fl-apps` bind-mount
means edits take effect immediately without a rebuild. A new job type needs no S3 sync: adding it to
`fl-apps/<backend>/` and its `required_files.json` is enough, because flip-api reads both locally.

### Makefile Reference

#### Network Management

| Command | Run from | Description |
| --------- | --------- | ------------- |
| `make -C fl-services/nvflare provision NET_NUMBER=X` | repo root | Provision FL network X |
| `make -C fl-services/nvflare provision-2-nets` | repo root | Provision both dev FL networks |
| `make build` | `fl-services/nvflare/` | Build the :dev FL images |
| `make up NET_NUMBER=X` | `fl-services/nvflare/` | Start FL network X |
| `make down NET_NUMBER=X` | `fl-services/nvflare/` | Stop FL network X (must match the `NET_NUMBER` used at `up`) |

#### Testing

| Command | Description |
| --------- | ------------- |
| `make unit-test` | Run pytest unit tests for flip python package |

#### Running tutorials (from the repo root)

| Command | Description |
| --------- | ------------- |
| `make -C fl-tutorials download-xray-data` | Fetch the xray_classification dataset from Hugging Face |
| `make -C fl-tutorials list-tutorials` | List the runnable NVFLARE tutorials |
| `make -C fl-tutorials run-tutorial TUTORIAL=<name>` | Run one tutorial on the local simulator |
| `make -C fl-tutorials run-all-tutorials` | Run every tutorial (heavy; stops on first failure) |

Each tutorial's dataset is downloaded per-tutorial — see the tutorial's own README.

---

## Security

Please report security vulnerabilities responsibly. For details on how to report a vulnerability, see [SECURITY.md](../SECURITY.md).

**⚠️ Do not open a public GitHub issue for security bugs; instead, use the private GitHub Security Advisory feature.**

---

## Contributing

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../CONTRIBUTING.md).

For anything specific to this package — versioning, the `develop` → `main` PR gates, how it is published to PyPI, and
how the release notes are assembled — see [`CONTRIBUTING.md`](CONTRIBUTING.md) in this directory.
