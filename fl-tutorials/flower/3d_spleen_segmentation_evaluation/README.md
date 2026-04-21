<!--
    Copyright (c) 2026 Flower Labs GmbH
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

---

tags: [quickstart]
dataset: [spleen]
framework: [monai]
---

# Federated Evaluation with MONAI and Flower

This example uses a MONAI UNet for 3D spleen segmentation in an evaluation-only mode. It loads pre-trained model checkpoints and performs federated evaluation across multiple client nodes. This app supports evaluating multiple models simultaneously.

## Key Features

- **Evaluation-only**: No training, only evaluation of pre-trained models
- **Multi-model support**: Evaluate multiple models in a single run
- **Type-safe metrics**: Metrics validation ensures proper data types (no strings allowed)
- **FLIP integration**: Uploads results to S3 and updates job status
- **WORKING_DIR environment variable**: Configurable output directory (default: `/app/runs`)

## Set up the project

### Folder structure

```shell
3d_spleen_segmentation_evaluation
├── app
│   ├── __init__.py
│   ├── client_app.py   # Defines your ClientApp (evaluation-only)
│   ├── config.json     # Configuration for evaluation job
│   ├── data_loading.py # MONAI transforms + datalist (test data only)
│   ├── models.py       # Defines model creation
│   ├── server_app.py   # Defines your ServerApp with checkpoint loading
│   ├── strategy.py     # Custom EvaluationStrategy with MetricsValidator
│   ├── task.py         # Defines evaluation functions
│   └── transforms.py   # MONAI transforms for preprocessing
├── pyproject.toml      # Project metadata like dependencies and configs
└── README.md
```

### Install dependencies and project

Install the dependencies defined in `pyproject.toml` as well as the `monai` package.

```bash
pip install -e .
```

## Running this tutorial

> **Run this tutorial with the Docker Compose stack, not `flwr run` / the
> Simulation Engine.** The compose stack is the supported path; the
> simulation path is documented below only so you understand why we avoid it.

### Recommended: Docker Compose

From the repository root:

```bash
make build                # build the fl-base / superlink / supernode images
make up                   # start fl-api, superlink, supernode-1, supernode-2
```

Submit the evaluation run against the `fl-api` control plane:

```bash
curl -X POST http://localhost:8000/submit_run/3d_spleen_segmentation_evaluation
```

The FLIP API will:

1. Load model checkpoints from the `model-checkpoints` directory
2. Run evaluation across all connected SuperNodes
3. Aggregate metrics using the `EvaluationStrategy`
4. Save results to `WORKING_DIR/{model_id}/evaluation_outputs/`
5. Upload results to S3 (unless `LOCAL_DEV="true"`)
6. Update job status via FLIP API

The compose file (`deploy/compose.yml`) wires everything correctly:

- `DEV_DATAFRAME`, `DEV_IMAGES_DIR`, `WORKING_DIR`, `MODEL_CHECKPOINTS_DIR`
  are resolved from `.env.flwr.development` via `${VAR}` substitutions in each
  service's `volumes:` block and bind-mounted into the containers.
- Inside the containers the mounts land at stable locations
  (`/images`, `/dataframe_file`, `/app/runs`, `/app/model_checkpoints`), and
  the `environment:` blocks point the app at those paths, so paths in the
  app resolve consistently regardless of your host layout.

### Not recommended: Flower Simulation Engine (`flwr run`)

We deliberately do **not** document a `flwr run` invocation for this tutorial.
Running it via the Simulation Engine is technically possible but brittle, for
reasons specific to this project:

1. **Long-lived `flower-superlink` caches its environment.** `flwr run`
   submits jobs to an already-running `flower-superlink` daemon. Ray worker
   subprocesses inherit the superlink's env, *not* the env you exported on
   the `flwr run` command line — so changing `DEV_DATAFRAME=…` between runs
   has no effect until you `pkill -f flower-superlink`.
2. **ClientApp CWD is not your project directory.** `flwr run` installs a
   snapshot of the app under `~/.flwr/apps/<publisher>.<name>.<version>.<hash>/`
   and runs ClientApp subprocesses from there, so relative paths like
   `../../data/...` resolve to `~/.flwr/data/...` and fail.
3. **FLIP's `DevSettings` singleton is pinned at import time.**
   `flip/constants/pt_constants.py` reads `FlipConstants.LOCAL_DEV` at
   class-body time, which forces pydantic-settings to materialise the
   singleton before any run starts. Once pinned, later `os.environ[...]`
   writes don't propagate, so mid-run path overrides are a dead end.

Under Docker Compose none of these bite: each container starts fresh, env
vars are applied from `env_file`/`environment:` at container start, and CWDs
are fixed by `working_dir:`. Use the compose stack above.

## Data Location

By default, the app reads from:

- `data/sample_get_dataframe_response.csv`
- `data/accession-resources`

## Architecture

### Strategy Pattern

The `EvaluationStrategy` in [strategy.py](tutorials/3d_spleen_segmentation_evaluation/app/strategy.py) handles:

1. **Metrics Validation**: `MetricsValidator` checks that all client metrics match the `metrics_spec` type definitions
2. **Distribution**: Sends packed model parameters to all clients
3. **Aggregation**: Collects and validates metrics from each client for each model
4. **Results Formatting**: Structures output as `{client_id: {model_name: {metric_name: value}}}`

### Multi-Model Evaluation

The server packs multiple models into a single `Parameters` object using the `pack_models()` function. Each model's weights are prefixed with `{model_name}/` to create unique keys. Clients use `unpack_model()` to extract weights for each model separately.

## Notes

- **Evaluation only** (no training or parameter updates)
- **Type-safe metrics**: Only `float`, `int`, or `list` types allowed - strings are rejected
- **Multi-model support**: Can evaluate multiple models in a single run
- **FLIP integration**: Automatically uploads results and updates job status
- **Environment-agnostic**: Uses `WORKING_DIR` instead of hardcoded paths

## Configuration

The evaluation metrics specification is defined using a type-based approach in `server_app.py`:

```python
metrics_spec = {
    "mean_dice": float,  # Single aggregated Dice score
    "raw_dice": list,  # List of per-slice Dice scores
}
```

The `MetricsValidator` class in `strategy.py` enforces that:

- All metrics match the specified types (float, int, or list)
- Strings are NOT allowed as metric values
- Each client returns metrics matching this specification for each model

### Environment Variables

- `MODEL_CHECKPOINTS_DIR`: Directory containing pre-trained model `.pt` files (mounted at `/app/model_checkpoints` on the SuperLink)
- `WORKING_DIR`: Output directory for evaluation results (default: `/app/runs`)
- `LOCAL_DEV`: Set to `"true"` to skip S3 uploads during local development
- `DEV_DATAFRAME`: Path to CSV file with test data metadata
- `DEV_IMAGES_DIR`: Path to directory containing NIfTI images

### Multi-Model Configuration

Models are specified in `pyproject.toml` using flattened keys:

```toml
[tool.flwr.app.config]
"models.spleen.checkpoint" = "model.pt"
"models.spleen.image_key" = "image"
"models.spleen.label_key" = "label"
```

The server automatically loads all models and packs them into a single parameters object for distribution to clients.

This template defines the structure of metrics that clients must return. The server validates that all returned metrics match this structure and contain the correct types (float or list of floats).
