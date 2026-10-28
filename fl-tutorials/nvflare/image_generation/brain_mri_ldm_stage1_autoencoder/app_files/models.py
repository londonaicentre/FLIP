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

"""Autoencoder network for the FLIP autoencoder tutorial: a KL autoencoder, a patch discriminator, and
the frozen MedicalNet backbone of the perceptual loss.

The autoencoder and discriminator are trained and aggregated together (``AGGREGATE_ONLY_REGEX``). The
backbone is not trained: the FL server loads it from ``SERVER_CHECKPOINT`` into the round-0 global
model, which is how its weights reach the clients. The autoencoder submodule must stay named
``autoencoder``: the latent diffusion tutorial loads this run's ``autoencoder.*`` weights by that
prefix, and its server loads with ``strict=False``, so a renamed submodule would load nothing.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from medicalnet_perceptual import build_backbone
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
    autoencoder = net_config["autoencoder"]
    return {
        "spatial_dims": net_config["spatial_dims"],
        "in_channels": autoencoder["in_channels"],
        "out_channels": autoencoder["out_channels"],
        "num_res_blocks": autoencoder["num_res_blocks"],
        "channels": autoencoder["channels"],
        "attention_levels": autoencoder["attention_levels"],
        "latent_channels": autoencoder["latent_channels"],
        "norm_num_groups": autoencoder["norm_num_groups"],
        "norm_eps": autoencoder["norm_eps"],
        "with_encoder_nonlocal_attn": autoencoder["with_encoder_nonlocal_attn"],
        "with_decoder_nonlocal_attn": autoencoder["with_decoder_nonlocal_attn"],
    }


class AutoencoderNetwork(nn.Module):
    """Creates the autoencoder training network, containing a:

    - Variational Autoencoder (compresses inputs into a latent space and reconstructs them)
    - Discriminator (adversarially trains the autoencoder)
    - Perceptual backbone (MedicalNet, frozen; scores reconstructions for the perceptual loss)
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
        self.perceptual = build_backbone()


_net = AutoencoderNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
