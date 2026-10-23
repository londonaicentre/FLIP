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
* **The autoencoder's architecture is not in ``config.json``.** It comes from
  ``autoencoder_config.yaml``, written next to the checkpoint by
  ``process_tools/extract_autoencoder.py`` and verified against that checkpoint's own tensors.
  A second copy of the block here could drift from the first, and disagreeing costs you nothing at
  load time and everything afterwards. Some of it cannot even be recovered from the weights —
  ``norm_num_groups`` changes how GroupNorm computes without changing a parameter shape, so the
  wrong value loads cleanly under ``strict=True`` and only corrupts the latents.

The trained submodule is ``diffusion_model``, which is what ``AGGREGATE_ONLY_REGEX`` in
``config.json`` selects for per-round aggregation.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from monai.networks.nets import AutoencoderKL, DiffusionModelUNet
from torch import nn

# Written beside the checkpoint by process_tools/extract_autoencoder.py, then staged here. Unlike the
# checkpoint — which SERVER_CHECKPOINT keeps server-side — this must reach every client, because
# clients build the bare architecture and receive the weights in the round-0 broadcast.
#
# The name is declared in config.json under AUTOENCODER_CONFIG, mirroring SERVER_CHECKPOINT for the
# weights, so the pair can be renamed together without editing code. This constant is only the
# fallback for a config that predates the key.
DEFAULT_AUTOENCODER_CONFIG_NAME = "autoencoder_config.yaml"

# The layout this module knows how to read. extract_autoencoder.py writes it.
_SUPPORTED_SCHEMA_VERSION = 1


def load_net_config():
    """Read the ``net_config`` block from the ``config.json`` next to this file."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


def autoencoder_config_name() -> str:
    """The architecture file's name, from ``config.json``'s ``AUTOENCODER_CONFIG``."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        return json.load(f).get("AUTOENCODER_CONFIG") or DEFAULT_AUTOENCODER_CONFIG_NAME


def load_autoencoder_config(path: Path | None = None) -> dict[str, Any]:
    """Read the frozen autoencoder's architecture from the YAML that accompanies the checkpoint.

    Args:
        path: The YAML to read. Defaults to the file ``config.json`` names, beside this one.

    Returns:
        The ``AutoencoderKL`` keyword arguments the checkpoint was trained with.

    Raises:
        FileNotFoundError: if the file is absent. This is deliberately fatal rather than defaulted:
            a guessed architecture loads the checkpoint as noise without raising, which is the exact
            failure this file exists to prevent.
        ValueError: if the schema version is one this module does not understand.
    """
    config_path = path or Path(__file__).parent / autoencoder_config_name()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"No {config_path.name} at {config_path}. This job trains a diffusion model inside "
            "a frozen autoencoder's latent space, so it needs to know that autoencoder's "
            "architecture; it is not duplicated in config.json on purpose. Produce it together with "
            "the checkpoint:\n"
            "    make prepare-checkpoint RAW_CHECKPOINT=<an `autoencoder` run's .pt>"
        )
    with open(config_path) as handle:
        document = yaml.safe_load(handle)

    version = document.get("schema_version")
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"{config_path} declares schema_version {version!r}, but this models.py reads "
            f"{_SUPPORTED_SCHEMA_VERSION}. Regenerate it with process_tools/extract_autoencoder.py."
        )
    architecture = document["autoencoder"]
    print(f"Loaded frozen autoencoder architecture from {config_path.name}: {architecture}")
    return architecture


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
        architecture = load_autoencoder_config()
        check_latent_geometry(net_config, architecture)
        # Built from the checkpoint's own config, so every argument matches the weights that arrive
        # in the round-0 broadcast — including the ones no later check could recover.
        self.autoencoder = AutoencoderKL(**architecture)

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
