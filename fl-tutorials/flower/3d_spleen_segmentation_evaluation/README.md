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

This example of Flower uses a small MONAI UNet based on FLIP's implementation and an evaluation-only `ClientApp`. It reads NIfTI data from the local `./data` folder and performs federated evaluation on a pre-trained model checkpoint.

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

Before running, you must set the `MODEL_CHECKPOINT_PATH` environment variable to point to a pre-trained model checkpoint file (`.pt` format).

Assuming the `./data` is at the top level directory of this repository, from the `3d_spleen_segmentation_evaluation` directory, use `flwr run` to run a local simulation:

```bash
MODEL_CHECKPOINT_PATH="/path/to/your/model.pt" \
DEV_DATAFRAME="../../data/spleen/sample_get_dataframe_response.csv" \
DEV_IMAGES_DIR="../../data/spleen/accession-resources" \
flwr run .
```

## Data Location

By default, the app reads from:

- `data/spleen/sample_get_dataframe_response.csv`
- `data/spleen/accession-resources`

## Notes

- **Evaluation only** (no training).
- Requires a pre-trained model checkpoint via `MODEL_CHECKPOINT_PATH` environment variable.
- Validates that metrics returned from clients are properly typed (floats or lists of floats).
- Aggregates evaluation results from all clients following the `evaluation_output` template defined in `pyproject.toml`.
- Uses test data split (configurable via `test-split` parameter in `pyproject.toml`).

## Configuration

The evaluation output template is defined in `pyproject.toml` and `config.json`:

```toml
[tool.flwr.app.config.evaluation_output]
spleen = { mean_dice = 0.0, raw_dice = [] }
```

This template defines the structure of metrics that clients must return. The server validates that all returned metrics match this structure and contain the correct types (float or list of floats).
