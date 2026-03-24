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
- **WORKING_DIR environment variable**: Configurable output directory (default: `/app`)

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

### Flower Config

If running locally (without Docker), ensure this SuperLink config is set in your `$HOME/.flwr/config.toml`:

```toml
[superlink]
default = "local-simulation"

[superlink.local-simulation]
options.num-supernodes = 2
federation = "@user/default"
```

### Install dependencies and project

Install the dependencies defined in `pyproject.toml` as well as the `monai` package.

```bash
pip install -e .
```

## Run with the Simulation Engine

### Local Development

For local testing without FLIP integration:

```bash
cd tutorials/3d_spleen_segmentation_evaluation
LOCAL_DEV="true" \
MODEL_CHECKPOINTS_DIR="../../data/model_checkpoints" \
DEV_DATAFRAME="../../data/sample_get_dataframe_response.csv" \
DEV_IMAGES_DIR="../../data/accession-resources" \
WORKING_DIR="/tmp/evaluation_outputs" \
flwr run .
```

### Production (with FLIP)

When running in a container with FLIP integration:

```bash
curl -X POST http://localhost:8000/submit_run/3d_spleen_segmentation_evaluation
```

The FLIP API will:
1. Load model checkpoints from `MODEL_CHECKPOINTS_DIR`
2. Run evaluation across all connected supernodes
3. Aggregate metrics using the `EvaluationStrategy`
4. Save results to `WORKING_DIR/{model_id}/evaluation_outputs/`
5. Upload results to S3 (unless `LOCAL_DEV="true"`)
6. Update job status via FLIP API

## Data Location

By default, the app reads from:

- `data/spleen/sample_get_dataframe_response.csv`
- `data/spleen/accession-resources`

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
    "raw_dice": list,    # List of per-slice Dice scores
}
```

The `MetricsValidator` class in `strategy.py` enforces that:
- All metrics match the specified types (float, int, or list)
- Strings are NOT allowed as metric values
- Each client returns metrics matching this specification for each model

### Environment Variables

- `MODEL_CHECKPOINTS_DIR`: Directory containing pre-trained model `.pt` files (default: `/app/model_checkpoints`)
- `WORKING_DIR`: Output directory for evaluation results (default: `/app`)
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
