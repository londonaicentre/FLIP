# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Latent diffusion network for the FLIP latent diffusion tutorial.

Composes a **frozen** autoencoder with the diffusion model that is actually trained. The
autoencoder compresses images into a latent space; the ``DiffusionModelUNet`` denoises within that
space, so its ``in_channels``/``out_channels`` are the autoencoder's ``latent_channels`` (3 here),
not the image channel count.

Two things about this network exist because the autoencoder is supplied rather than trained:

* **There is no discriminator.** It only ever served the autoencoder's adversarial training, which
  happens in the separate `autoencoder` tutorial. The checkpoint uploaded here is therefore
  autoencoder-only.
* **The submodule must be named ``autoencoder``**, matching the `autoencoder` tutorial's network, so
  the uploaded checkpoint's ``autoencoder.*`` keys land on it. ``InitialCheckpointPTModelPersistor``
  loads with ``strict=False``, so a name or shape mismatch is *silently tolerated* — the diffusion
  model would then train against a randomly-initialised encoder while reporting plausible losses.
  ``net_config.stage_1`` must likewise match that tutorial's block exactly.

The trained submodule is ``diffusion_model``, which is what ``AGGREGATE_ONLY_REGEX`` in
``config.json`` selects for per-round aggregation.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from monai.networks.nets import AutoencoderKL, DiffusionModelUNet
from torch import nn


def load_net_config():
    """Read the ``net_config`` block from the ``config.json`` next to this file."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


class LatentDiffusionModelNetwork(nn.Module):
    """Creates a latent diffusion model containing a:
    - Variational Autoencoder (to compress inputs into latent space) — FROZEN, supplied via
      ``SERVER_CHECKPOINT``
    - Diffusion Model (to generate samples in the latent space) — the module actually trained
    """

    def __init__(self):
        super().__init__()
        net_config = load_net_config()
        self.autoencoder = AutoencoderKL(
            spatial_dims=net_config["spatial_dims"],
            in_channels=net_config["stage_1"]["in_channels"],
            out_channels=net_config["stage_1"]["out_channels"],
            num_res_blocks=net_config["stage_1"]["num_res_blocks"],
            channels=net_config["stage_1"]["channels"],
            attention_levels=net_config["stage_1"]["attention_levels"],
            latent_channels=net_config["stage_1"]["latent_channels"],
            with_encoder_nonlocal_attn=False,
            with_decoder_nonlocal_attn=False,
        )

        self.diffusion_model = DiffusionModelUNet(
            spatial_dims=net_config["spatial_dims"],
            in_channels=net_config["diffusion_model"]["in_channels"],
            out_channels=net_config["diffusion_model"]["out_channels"],
            with_conditioning=net_config["diffusion_model"]["with_conditioning"],
            cross_attention_dim=None
            if net_config["diffusion_model"]["cross_attention_dim"] == 0
            else net_config["diffusion_model"]["cross_attention_dim"],
            channels=net_config["diffusion_model"]["channels"],
            num_res_blocks=net_config["diffusion_model"]["num_res_blocks"],
            attention_levels=net_config["diffusion_model"]["attention_levels"],
        )

    def forward_ae(self, x):
        return self.autoencoder(x)

    def forward_dm(self, x):
        return self.diffusion_model(x)

    def load_autoencoder_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.autoencoder.load_state_dict(state_dict, strict=strict)

    def load_diffusion_model_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.diffusion_model.load_state_dict(state_dict, strict=strict)


_net = LatentDiffusionModelNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
