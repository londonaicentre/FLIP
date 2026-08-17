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

This is the NVFLARE **Client API** FedOpt job type (`JOB_TYPE=fed_opt`). It differs from
[`standard`](../standard/README.md) (Federated Averaging) in that the server holds a persistent
optimizer — and optionally a learning-rate scheduler — and updates the global model by running
that optimizer on the averaged client weight-diffs (treated as a pseudo-gradient), instead of
replacing the global weights with the aggregate.

It is based on the paper "Adaptive Federated Optimization" by Reddi S. et al. (2020), and on
NVFlare's implementation in their CIFAR10 tutorial available at:
<https://github.com/NVIDIA/NVFlare/tree/2.4/examples/advanced/cifar10/cifar10-sim/jobs/cifar10_fedopt/cifar10_fedopt>.

It replaced the retired Executor-based `fed_opt` template. The **client contract is identical to
`standard`**: a Client API `trainer.py` that sends its update as
`FLModel(params=diff, params_type="DIFF")` — any `standard` app or tutorial runs under `fed_opt`
unchanged (upload the same files with `"job_type": "fed_opt"` in `config.json`).

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipFedOptRecipe`. Do not
hand-edit them — regenerate via `recipe.py` after any recipe change and commit the result.

## How the aggregation differs from `standard`

- The aggregator accepts the clients' `WEIGHT_DIFF` DXOs directly (stock
  `InTimeAccumulateWeightedAggregator` default) — as every FLIP job type now does; the difference
  is purely in how the averaged diff is applied.
- The shareable generator is `flip.nvflare.components.FlipFedOptShareableGenerator` (stock
  `PTFedOptModelShareableGenerator` extended to source the model from the user's
  `models.get_model`), which applies the averaged diff to the global model through the configured
  server optimizer. Defaults follow stock NVFLARE FedOpt: `torch.optim.SGD` at `lr=1.0` with
  `momentum=0.6`, no LR scheduler, on a CPU-held model copy — override via
  `FlipFedOptRecipe(optimizer_args=..., lr_scheduler_args=...)` and regenerate. (The retired
  Executor template declared server Adam at `lr=0.5`, but its aggregation never actually ran —
  see the `ScatterAndGather` guard fix in this migration — and once live that setting destroys
  the global model in one step, so it was not carried forward.)

## Execution sequence

The control flow is the same as the [`standard`](../standard/README.md) job — the FedOpt
difference is the server-side shareable generator / optimizer (a component), not the workflow
order.

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `InitTraining` — `flip.nvflare.controllers.InitTraining`
2. `ScatterAndGather` — `flip.nvflare.controllers.ScatterAndGather`
3. stock `nvflare.app_common.workflows.global_model_eval.GlobalModelEval`
4. `BroadcastTask` post-validation cleanup — `flip.nvflare.controllers.BroadcastTask`

**Client — `config_fed_client.json` `executors` (by task):**

- `init_training`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `train`, `validate` → `nvflare.app_common.executors.InProcessClientAPIExecutor` running
  `custom/trainer.py`

Post-training evaluation covers only the aggregated global model at each participating trust;
client-local models are not collected for an all-to-all matrix.

## Run / test it

`fed_opt` has no tutorial of its own, but every `standard` tutorial's app is compatible. To
exercise it on the platform, upload e.g. the spleen tutorial's `app_files/` with `config.json`'s
`job_type` set to `fed_opt`. Locally, build the job with `FlipFedOptRecipe` in place of
`FlipFedAvgRecipe` in any `standard` tutorial's `job.py`.
