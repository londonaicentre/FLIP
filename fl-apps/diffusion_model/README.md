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

# Latent diffusion model

This app allows to train a two-stage diffusion model from a single validator and trainer file.
The `scatter_and_gather` function has been modified to persist the first stage (autoenocder-like network) to train the second stage (diffusion model).

As this training has two stages, there are global and local rounds specific for each stage.

This code is compatible with a single `trainer.py` and `validator.py` files with training loops for different phases, and `models.py` files containing the different stages under the same network.

## Execution sequence

Two stages run back to back: the autoencoder (`_ae`) is trained and validated first, then the diffusion model (`_dm`).

**Server — `config_fed_server.json` `workflows` (run in order):**

1. `init_training` — `flip.nvflare.controllers.InitTraining`
2. `scatter_and_gather_ae` — `flip.nvflare.controllers.ScatterAndGatherLDM` (stage 1: autoencoder)
3. `cross_site_validate_ae` — `flip.nvflare.controllers.CrossSiteModelEval`
4. `scatter_and_gather_dm` — `flip.nvflare.controllers.ScatterAndGatherLDM` (stage 2: diffusion)
5. `cross_site_validate_dm` — `flip.nvflare.controllers.CrossSiteModelEval`

**Client — `config_fed_client.json` `executors` (by task):**

- `init_training`, `post_validation` → `flip.nvflare.components.CleanupImages`
- `train_ae` → `flip.nvflare.executors.RUN_TRAINER`
- `train_dm`, `submit_model` → `flip.nvflare.executors.RUN_TRAINER`
- `validate_ae` → `flip.nvflare.executors.RUN_VALIDATOR`
- `validate_dm` → `flip.nvflare.executors.RUN_VALIDATOR`

## Validation metrics

For security purposes, plotting is disable in production, with metrics being the only thing being sent to the server.
For the stage 1, both the L1 loss value and SSIM metrics are sent.
For the stage 2 (diffusion), we send the L1 loss value.

When using this app in dev mode (`LOCAL_DEV=True`), VAE ground truth vs. reconstruction and diffusion model samples are
plotted in the client folder.

## Requirements

See [requirements.txt](./app/custom/requirements.txt) for the full list of dependencies.

Note `matplotlib` is only available in dev mode for security reasons, and is not installed in production.
