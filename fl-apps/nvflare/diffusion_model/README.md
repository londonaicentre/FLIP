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

# Client API latent diffusion model (two-stage)

## Overview

This is the NVFLARE **Client API** latent-diffusion job type (`JOB_TYPE=diffusion_model`).
It runs a **two-stage** federated training — an autoencoder (+ GAN discriminator)
FedAvg stage followed by a diffusion-model FedAvg stage over the frozen autoencoder's latent space,
each stage with its own cross-site validation — driving the clients through the NVFLARE Client
API (`InProcessClientAPIExecutor`). It replaced the retired Executor-based `diffusion_model`
template, which drove the clients through the legacy `RUN_TRAINER`/`RUN_VALIDATOR` executor pairs.

The base configs (`app/config/config_fed_server.json`, `app/config/config_fed_client.json`) and
`meta.json` are **recipe-generated** from `flip.nvflare.recipes.FlipDiffusionRecipe`. Do not
hand-edit them — regenerate via `recipe.py` after any recipe change and commit the result.

## What's the logic?

The two stages run back to back on the server, sharing one persistor — the `train_dm` controller
seeds itself with the autoencoder weights the `train_ae` controller persisted (the stage-1 → stage-2
handoff):

1. Stage 1 (`train_ae`): each site trains the autoencoder + discriminator locally; the server
   aggregates and, when the stage's rounds finish, cross-site-validates the aggregated autoencoder
   (`validate_ae`).
2. Stage 2 (`train_dm`): each site trains the diffusion model over the frozen autoencoder's latent
   space; the server aggregates and cross-site-validates (`validate_dm`).

Per-stage round counts come from the user `config.json` (`GLOBAL_ROUNDS_AE` / `GLOBAL_ROUNDS_DM`
globally, `LOCAL_ROUNDS_AE` / `LOCAL_ROUNDS_DM` per site) — the `ScatterAndGatherLDM` controllers
re-read them at start, so the values baked into the template are simulator defaults only.

Unlike the single-phase Client API templates, **one client script serves four task names**
(`train_ae` / `train_dm` / `validate_ae` / `validate_dm`): the single-name `flare.is_train()` /
`flare.is_evaluate()` predicates cannot distinguish the two train stages, so the user's `trainer.py`
dispatches on `nvflare.client.api.get_task_name()`. `validator.py` is still required — it holds the
validation passes (and shared latent-geometry helpers) that `trainer.py` imports; it is a plain
module, not an NVFLARE component.

Training results return as a full-model weight **diff** (`params_type="DIFF"`; the aggregator
averages the clients' `WEIGHT_DIFF` updates directly — the stock default — and the shareable
generator applies the average to the global model), filtered through `StagePercentilePrivacy` — the stage-aware DP filter computes its percentile
cutoff over exactly the modules the stage trained, scoped by the `FlipMetaKey.STAGE` meta the
script stamps on the outgoing `FLModel`.

## Execution sequence

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `flip.nvflare.controllers.InitTraining`
2. `flip.nvflare.controllers.ScatterAndGatherLDM` (stage 1: autoencoder, `train_ae`)
3. stock `nvflare.app_common.workflows.global_model_eval.GlobalModelEval` (`validate_ae`)
4. `flip.nvflare.controllers.BroadcastTask` (`post_validation` cleanup)
5. `flip.nvflare.controllers.ScatterAndGatherLDM` (stage 2: diffusion, `train_dm`)
6. stock `GlobalModelEval` (`validate_dm`)
7. `flip.nvflare.controllers.BroadcastTask` (`post_validation` cleanup)

**Client — `config_fed_client.json` `executors` / `filters`:**

- `init_training`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `train_ae`, `train_dm`, `validate_ae`, `validate_dm` → ONE
  `nvflare.app_common.executors.InProcessClientAPIExecutor` running `custom/trainer.py`
- `train_ae`/`train_dm` results → `flip.nvflare.components.StagePercentilePrivacy` (stage-aware DP
  noise filter)
- Event handlers: `ClientEventHandler`, `FlipAnalyticsBridge`

## Required user files

| File | Role |
| --- | --- |
| `trainer.py` | The Client API script: `flare.init()` loop dispatching all four tasks on `get_task_name()` |
| `validator.py` | Validation passes + latent-geometry helpers imported by `trainer.py` |
| `models.py` | `get_model()` returning the composite network (`autoencoder` / `discriminator` / `diffusion_model` sub-modules) |
| `config.json` | `job_type`, per-stage rounds, learning rates, loss weights, `net_config`, `spatial_shape` |

Reference implementation: the
[`latent_diffusion_model`](../../../fl-tutorials/nvflare/image_synthesis/latent_diffusion_model/)
tutorial.
