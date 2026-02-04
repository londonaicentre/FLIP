<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

## Validation metrics

For security purposes, plotting is disable in production, with metrics being the only thing being sent to the server.
For the stage 1, both the L1 loss value and SSIM metrics are sent.
For the stage 2 (diffusion), we send the L1 loss value.

When using this app in dev mode (`LOCAL_DEV=True`), VAE ground truth vs. reconstruction and diffusion model samples are
plotted in the client folder.

## Requirements

`monai > 1.3`

`einops`

`gdown`

`matplotlib` * DEV mode only.

`lpips`