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

This example uses a MONAI UNet for 3D spleen segmentation in an evaluation-only mode. It loads a pre-trained model checkpoint and performs federated evaluation across multiple client nodes.

## Key Features

- **Evaluation-only**: No training, only evaluation of a pre-trained model
- **Customisable metrics**: the metrics are whatever `client_app`/`task.py` compute and return (this tutorial reports Dice and IoU); the server aggregates them natively
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
│   ├── strategy.py     # EvaluationStrategy: native FedAvg aggregation + FLIP forwarding
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
make -C tutorials/3d_spleen_segmentation_evaluation download-checkpoints  # fetch model.pt into the app
make up                   # start fl-api, superlink, supernode-1, supernode-2
```

`download-checkpoints` places `model.pt` in the `app/` folder, where the
evaluation ServerApp reads it (via the `flip-job-dir` run-config value).

Submit the evaluation run against the `fl-api` control plane:

```bash
make submit APP=3d_spleen_segmentation_evaluation
```

The default stack publishes no host ports; `make submit` execs into the fl-api
container and POSTs to its loopback API. Use `make up-debug` instead if you want
to POST from the host (`curl -X POST http://localhost:8000/submit_tutorial/3d_spleen_segmentation_evaluation`)
or open the Swagger UI.

The FLIP API will:

1. Load the model checkpoint named by the `checkpoint` run-config value
2. Run evaluation across all connected SuperNodes
3. Aggregate metrics using the `EvaluationStrategy`
4. Save results to `WORKING_DIR/{model_id}/evaluation_outputs/`
5. Upload results to S3 (unless `LOCAL_DEV="true"`)
6. Update job status via FLIP API

The compose file (`deploy/compose.yml`) wires everything correctly:

- `DEV_DATAFRAME`, `DEV_IMAGES_DIR`, `WORKING_DIR`
  are resolved from `.env.flwr.development` via `${VAR}` substitutions in each
  service's `volumes:` block and bind-mounted into the containers.
- Inside the containers the mounts land at stable locations
  (`/images`, `/dataframe_file`, `/app/runs`), and
  the `environment:` blocks point the app at those paths, so paths in the
  app resolve consistently regardless of your host layout.
- Uploaded app bundles (sources + model checkpoint) live under
  `/app/src/<model_id>`; the FL API and the SuperLink share the `/app/src`
  volume, and the evaluation ServerApp reads the checkpoint from the path in the
  `flip-job-dir` run-config value the FL API injects at submission time.

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

`EvaluationStrategy` in [strategy.py](app/strategy.py) is a thin subclass of Flower's
`FedAvg`. `FedAvg` already distributes the model and aggregates every metric the clients
return — a weighted average by `num-examples` — so the subclass adds only:

1. **FLIP forwarding**: `handle_client_metrics` / `handle_client_exception` push each
   client's metrics and any exceptions to the Central Hub. These need the full reply
   message, so they live in the `aggregate_evaluate` override.
2. **Per-client breakdown**: captures `{client_name: {metric_name: value}}` for the
   `evaluation_results.json` artifact uploaded to S3.

The metrics themselves are simply whatever `client_app.py` puts in its `MetricRecord` —
there is no metric declaration or validation.

## Notes

- **Evaluation only** (no training or parameter updates)
- **Metrics**: `MetricRecord` only accepts numeric values; the server aggregates whatever metric keys the client returns
- **FLIP integration**: Automatically uploads results and updates job status
- **Environment-agnostic**: Uses `WORKING_DIR` instead of hardcoded paths

## Configuration

### Metrics

Metrics are not declared in config. The client computes them in [`task.py`](app/task.py)
and returns them in its `MetricRecord`; the server's `FedAvg` strategy averages whatever
keys arrive (weighted by `num-examples`). To change which metrics are reported, edit the
`_METRICS` registry in `task.py`:

```python
_METRICS = {
    "mean_dice": lambda: DiceMetric(include_background=False, reduction="mean_batch"),
    "mean_iou": lambda: MeanIoU(include_background=False, reduction="mean_batch"),
}
```

Each metric name becomes a key in the aggregated results and a `LABEL` on the FLIP
Central Hub (e.g. `MEAN_DICE`, `MEAN_IOU`). No server or `pyproject.toml` change is needed.

### Environment Variables

- `flip-job-dir`: run-config value (in `config.toml`, not an env var) injected by the FL API at submission time; points at the app directory (`/app/src/<model_id>/app` on the shared volume) where the evaluation ServerApp reads the pre-trained model `.pt` file
- `WORKING_DIR`: Output directory for evaluation results (default: `/app/runs`)
- `LOCAL_DEV`: Set to `"true"` to skip S3 uploads during local development
- `DEV_DATAFRAME`: Path to CSV file with test data metadata
- `DEV_IMAGES_DIR`: Path to directory containing NIfTI images

### Checkpoint Configuration

The checkpoint to evaluate is set with a single `checkpoint` key under
`[tool.flwr.app.config]` — a dummy placeholder in `pyproject.toml`, overridden
per run in `app/config.toml`:

```toml
[tool.flwr.app.config]
checkpoint = "model.pt"
```

The ServerApp loads that file from `flip-job-dir` and distributes its weights to the clients.
