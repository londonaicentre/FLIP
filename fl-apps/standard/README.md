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

# Standard federated training (FedAvg)

## Overview

This is the baseline job type (`JOB_TYPE=standard`) and the default for most FLIP models. It performs
supervised **Federated Averaging**: each site trains the model locally on its own data, and the server
aggregates the returned weights into a new global model by weighted averaging. The other templates build
on this one — for example [`fed_opt`](../fed_opt/README.md) swaps the server-side aggregation for an
adaptive optimizer.

## What's the logic?

For each global round (`global_rounds` in `config_fed_server.json`):

1. The server sends the current global model to every site.
2. Each site runs the user's `trainer.py` for `local_rounds` (via `flip.nvflare.executors.RUN_TRAINER`),
   then evaluates with `validator.py` (via `flip.nvflare.executors.RUN_VALIDATOR`).
3. Sites return their updated weights (`DataKind.WEIGHTS`).
4. The server aggregates them with the `InTimeAccumulateWeightedAggregator` (weighted by each site's number
   of samples) and persists the new global model with `PTFileModelPersistor`.

Orchestration is the standard NVFLARE Scatter-and-Gather workflow; the privileged image cleanup runs via
`flip.nvflare.components.CleanupImages` on the `init_training` / `post_validation` tasks.

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `trainer.py` — the local training loop. Return the trained weights as `DataKind.WEIGHTS`.
- `validator.py` — the local validation/test loop, returning metrics to the server.
- `models.py` — defines the model; `models.get_model` is what the server persistor instantiates.
- `config.json` — model/training configuration consumed by the custom code (e.g. `global_rounds`,
  hyper-parameters, the cohort `query`).

The base config (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) ships with the
template; the files above are merged in on top at job-assembly time.

## Run it

The xray-classification and 3D spleen-segmentation tutorials both use this job type. Run one on the local
NVFLARE simulator:

```bash
make -C fl-tutorials download-xray-data
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
```

See [`fl-tutorials/`](../../fl-tutorials/) for the available tutorials and their datasets.
