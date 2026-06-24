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

# Adaptive Federated Optimization (FedOpt)

## Overview

This app differs from Federated Averaging in that the server has a persistent optimizer and, optionally, a learning rate scheduler that update the global weight running the optimizer on the difference between global and local gradients.

It is based on the paper "Adaptive Federated Optimization" by Reddi S. et al. (2020), and on NVFlare's implementation in their CIFAR10 tutorial available at: <https://github.com/NVIDIA/NVFlare/tree/2.4/examples/advanced/cifar10/cifar10-sim/jobs/cifar10_fedopt/cifar10_fedopt>.

## Execution sequence

The control flow is the same as the [`standard`](../standard/README.md) job — the FedOpt difference is the server-side shareable generator / optimizer (a component), not the workflow order.

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `init_training` — `flip.nvflare.controllers.InitTraining`
2. `scatter_and_gather` — `flip.nvflare.controllers.ScatterAndGather`
3. `cross_site_validate` — `flip.nvflare.controllers.CrossSiteModelEval`

**Client — `config_fed_client.json` `executors` (by task):**

- `init_training`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `train`, `submit_model` → `flip.nvflare.executors.RUN_TRAINER`
- `validate` → `flip.nvflare.executors.RUN_VALIDATOR`

## Technical differences

`fed_opt` differs from [`standard`](../standard/README.md) (FedAvg) only in the **server-side weight update**:
plain averaging is replaced by a server optimizer that treats the aggregated update as a pseudo-gradient. The
control flow (workflows / executors) and the uploaded `trainer.py` / `validator.py` / `models.py` are the same.

| | `standard` (FedAvg) | `fed_opt` (FedOpt) |
| --- | --- | --- |
| Shareable generator | `FullModelShareableGenerator` | `PTFedOptModelShareableGenerator` |
| Server aggregation | average the returned weights | server **Adam** optimizer (`lr=0.5`, betas 0.9/0.999) + **ExponentialLR** scheduler (`gamma=0.995`) over the aggregated update |
| `expected_data_kind` | `WEIGHTS` | `WEIGHT_DIFF` |
| Extra component | — | `IntimeModelSelector` (keeps the best global model across rounds) |
| `global_rounds` | 3 | 5 |
| `min_clients` | 1 | 2 |

The optimizer and scheduler are customisable in `config_fed_server.json` (the learning rate must be between 0.1
and 1.0).

## Changes to trainer / validator

To use FedOpt, the trainer has to commit the weights differences between the local and global models in the form of `DataKind` `WEIGHT_DIFF`.

Make sure to compute weight differences using the `get_model_weights_diff` function from `flip.utils.model_weights_handling`.

Note that the validator does not need to be changed.

## Run / test it

`fed_opt` has no tutorial of its own, but the 3D spleen-segmentation tutorial's app_files are FedOpt-compatible
(its `trainer.py` already emits `WEIGHT_DIFF`). Smoke-test the template on the local NVFLARE simulator with:

```bash
make -C fl-tutorials download-spleen-data
make -C fl-tutorials test-template TEMPLATE=fed_opt
```

This merges `fl-apps/fed_opt/app` with the spleen tutorial's app_files + data and runs it on the simulator
(requires GPUs + the `flare-fl-base` image).
