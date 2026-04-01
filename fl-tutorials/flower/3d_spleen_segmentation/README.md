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

# Federated Learning with MONAI and Flower (Quickstart Example)

This example of Flower uses a small MONAI UNet based on FLIP's implementation and a training-only `ClientApp`. It reads NIfTI data from the local `./data` folder and does not write any outputs.

## Set up the project

### Folder structure

```shell
3d_spleen_segmentation
├── app
│   ├── __init__.py
│   ├── client_app.py   # Defines your ClientApp
│   ├── data_loading.py # MONAI transforms + datalist
│   ├── get_data.py     # Placeholder function for FLIP API, reads local data
│   ├── server_app.py   # Defines your ServerApp
│   └── task.py         # Defines model creation
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

Assuming the `./data` is at the top level directory of this repository, and that  from the `3d_spleen_segmentation` directory, use `flwr run` to run a local simulation:

```bash
DEV_DATAFRAME="../../data/spleen/sample_get_dataframe_response.csv"  DEV_IMAGES_DIR="../../data/spleen/accession-resources" WORKING_DIR="../../data/" flwr run .
```

## Data Location

By default, the app reads from:

- `data/spleen/sample_get_dataframe_response.csv`
- `data/spleen/accession-resources`
