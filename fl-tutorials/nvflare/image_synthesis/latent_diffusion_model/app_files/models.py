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

"""Latent diffusion network for the FLIP latent diffusion tutorial: a frozen autoencoder and the UNet that is trained.

The UNet denoises in the autoencoder's latent space, so its in and out channels are the autoencoder's
``latent_channels``. The autoencoder is built from ``autoencoder_config.yaml``, which
``process_tools/extract_autoencoder.py`` writes beside its checkpoint, not from ``config.json``. Its
submodule is named ``autoencoder`` so the checkpoint's ``autoencoder.*`` keys land on it. There is no
discriminator: that only served the autoencoder's own training. ``AGGREGATE_ONLY_REGEX`` selects
``diffusion_model`` for aggregation.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from monai.networks.nets import AutoencoderKL, DiffusionModelUNet
from torch import nn

# Written beside the checkpoint by process_tools/extract_autoencoder.py. Unlike the checkpoint, which
# stays on the FL server, it reaches every client: they build the bare autoencoder from it.
AUTOENCODER_CONFIG = Path(__file__).parent / "autoencoder_config.yaml"


def load_net_config():
    """Read the ``net_config`` block from the ``config.json`` next to this file."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


def read_autoencoder_document(path: Path = AUTOENCODER_CONFIG) -> dict[str, Any]:
    """Read ``autoencoder_config.yaml``: the ``autoencoder`` architecture and its ``autoencoder_sha256``.

    Raises:
        FileNotFoundError: if it is absent. Deliberately fatal: a guessed architecture would load the
            checkpoint as noise without raising.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"No {path.name} at {path}. This job needs the frozen autoencoder's architecture, which "
            "comes with its checkpoint:\n    make prepare-checkpoint RAW_CHECKPOINT=<an `autoencoder` run's .pt>"
        )
    with open(path) as handle:
        document = yaml.safe_load(handle)
    print(f"Loaded frozen autoencoder architecture from {path.name}: {document['autoencoder']}")
    return document


def check_latent_geometry(net_config: Mapping[str, Any], architecture: Mapping[str, Any]) -> None:
    """Fail loudly where ``config.json`` and the autoencoder's config have to agree.

    Two couplings are only true by convention, and both are cheap to state:

    * the UNet denoises *in the latent space*, so its channel count is the autoencoder's
      ``latent_channels``. Mismatching raises deep inside a training step instead of here.
    * both networks see the same data, so their ``spatial_dims`` cannot differ.
    """
    latent_channels = architecture["latent_channels"]
    diffusion = net_config["diffusion_model"]
    for end in ("in_channels", "out_channels"):
        if diffusion[end] != latent_channels:
            raise ValueError(
                f"net_config.diffusion_model.{end} is {diffusion[end]}, but the frozen autoencoder "
                f"produces {latent_channels} latent channels. The UNet denoises latents, not images."
            )
    if net_config["spatial_dims"] != architecture["spatial_dims"]:
        raise ValueError(
            f"net_config.spatial_dims is {net_config['spatial_dims']} but the frozen autoencoder was "
            f"built for {architecture['spatial_dims']}-D data."
        )


class LatentDiffusionModelNetwork(nn.Module):
    """Creates a latent diffusion model containing a:
    - Variational Autoencoder (to compress inputs into latent space) — FROZEN, supplied via
      ``SERVER_CHECKPOINT``
    - Diffusion Model (to generate samples in the latent space) — the module actually trained
    """

    def __init__(self):
        super().__init__()
        net_config = load_net_config()
        document = read_autoencoder_document()
        architecture = document["autoencoder"]
        check_latent_geometry(net_config, architecture)
        # Built from the checkpoint's own config, so every argument matches the weights that arrive
        # in the round-0 broadcast — including the ones no later check could recover.
        self.autoencoder = AutoencoderKL(**architecture)
        # The fingerprint of those weights; the trainer checks what actually arrived against it.
        self.autoencoder_sha256 = document["autoencoder_sha256"]

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


_net = LatentDiffusionModelNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
