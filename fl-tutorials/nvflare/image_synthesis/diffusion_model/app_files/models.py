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

"""Diffusion model network for the FLIP pixel-space diffusion tutorial.

A single ``DiffusionModelUNet`` that denoises **images directly**. There is no autoencoder and no
discriminator here: the diffusion process runs at full image resolution, so nothing needs to be
compressed first and nothing is adversarially trained.

Because the UNet sees images rather than latents, its ``in_channels``/``out_channels`` are the
**image** channel count (1 for the single-channel chest X-rays this tutorial uses), not an
autoencoder's ``latent_channels``. Contrast the latent diffusion tutorial, where the same network
operates on a 3-channel latent.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from monai.networks.nets import DiffusionModelUNet
from torch import nn


def load_net_config():
    """Read the ``net_config`` block from the ``config.json`` next to this file."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


class DiffusionModelNetwork(nn.Module):
    """Creates a pixel-space diffusion model: a single UNet denoising images directly."""

    def __init__(self):
        super().__init__()
        net_config = load_net_config()
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

    def forward_dm(self, x):
        return self.diffusion_model(x)

    def load_diffusion_model_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.diffusion_model.load_state_dict(state_dict, strict=strict)


_net = DiffusionModelNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
