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

"""Autoencoder network for the FLIP autoencoder tutorial: a KL autoencoder and a patch discriminator.

Both are trained and aggregated together. The autoencoder submodule must stay named ``autoencoder``:
the latent diffusion tutorial loads this run's ``autoencoder.*`` weights by that prefix, and its
server loads with ``strict=False``, so a renamed submodule would load nothing.
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


def autoencoder_kwargs(net_config: Mapping[str, Any]) -> dict[str, Any]:
    """The keyword arguments this tutorial builds its ``AutoencoderKL`` with: the one definition.

    The latent diffusion tutorial's ``extract_autoencoder.py`` records them beside an extracted
    checkpoint. Every argument is explicit: ``norm_num_groups`` changes no tensor shape, so a wrong
    default would load a checkpoint cleanly and produce wrong latents.
    """
    stage_1 = net_config["stage_1"]
    return {
        "spatial_dims": net_config["spatial_dims"],
        "in_channels": stage_1["in_channels"],
        "out_channels": stage_1["out_channels"],
        "num_res_blocks": stage_1["num_res_blocks"],
        "channels": stage_1["channels"],
        "attention_levels": stage_1["attention_levels"],
        "latent_channels": stage_1["latent_channels"],
        "norm_num_groups": stage_1["norm_num_groups"],
        "norm_eps": stage_1["norm_eps"],
        "with_encoder_nonlocal_attn": stage_1["with_encoder_nonlocal_attn"],
        "with_decoder_nonlocal_attn": stage_1["with_decoder_nonlocal_attn"],
    }


class AutoencoderNetwork(nn.Module):
    """Creates the autoencoder training network, containing a:

    - Variational Autoencoder (compresses inputs into a latent space and reconstructs them)
    - Discriminator (adversarially trains the autoencoder)
    """

    def __init__(self):
        super().__init__()
        net_config = load_net_config()
        self.autoencoder = AutoencoderKL(**autoencoder_kwargs(net_config))

        self.discriminator = PatchDiscriminator(
            spatial_dims=net_config["discriminator"]["spatial_dims"],
            in_channels=net_config["discriminator"]["in_channels"],
            channels=net_config["discriminator"]["channels"],
            out_channels=net_config["discriminator"]["out_channels"],
            num_layers_d=net_config["discriminator"]["num_layers_d"],
        )


_net = AutoencoderNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
