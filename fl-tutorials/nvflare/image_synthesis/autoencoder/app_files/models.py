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

"""Autoencoder network for the FLIP autoencoder (VAE) tutorial.

The network pairs a KL-regularised autoencoder with a patch discriminator that adversarially
sharpens its reconstructions. Both are trained and aggregated together — unlike the latent
diffusion tutorial, nothing here is frozen.

The submodule holding the autoencoder is deliberately named ``autoencoder``, so its parameters
are keyed ``autoencoder.*`` in the state dict. That name is a **contract with the latent
diffusion tutorial**: a run of this tutorial produces the checkpoint that one loads as its frozen
encoder (via ``SERVER_CHECKPOINT``), and the keys line up only because both networks name the
submodule identically. See ``process_tools/extract_autoencoder.py`` there. Renaming this
attribute silently breaks that handoff — the load is ``strict=False``, so a mismatch is tolerated
rather than raised, and the diffusion model then trains against a randomly-initialised encoder.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from monai.networks.nets import AutoencoderKL, PatchDiscriminator
from torch import nn


def load_net_config():
    """Read the ``net_config`` block from the ``config.json`` next to this file."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


class AutoencoderNetwork(nn.Module):
    """Creates the autoencoder training network, containing a:

    - Variational Autoencoder (compresses inputs into a latent space and reconstructs them)
    - Discriminator (adversarially trains the autoencoder)
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

        self.discriminator = PatchDiscriminator(
            spatial_dims=net_config["discriminator"]["spatial_dims"],
            in_channels=net_config["discriminator"]["in_channels"],
            channels=net_config["discriminator"]["channels"],
            out_channels=net_config["discriminator"]["out_channels"],
            num_layers_d=net_config["discriminator"]["num_layers_d"],
        )

    def forward_ae(self, x):
        return self.autoencoder(x)

    def discriminate(self, x):
        return self.discriminator(x)

    def load_autoencoder_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.autoencoder.load_state_dict(state_dict, strict=strict)

    def load_discriminator_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.discriminator.load_state_dict(state_dict, strict=strict)


_net = AutoencoderNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
