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

import json
from pathlib import Path
from typing import Any, Mapping

from monai.networks.nets import AutoencoderKL, DiffusionModelUNet, PatchDiscriminator
from torch import nn


# Here is where we can load the config file with network params if necessary, for example:
def load_net_config():
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


class LatentDiffusionModelNetwork(nn.Module):
    """Creates a multi-stage latent diffusion model containing a:
    - Variational Autoencoder (to compress inputs into latent space)
    - Discriminator (to adversarially train the autoencoder)
    - Diffusion Model (to generate samples in the latent space)
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

    def discriminate(self, x):
        return self.discriminator(x)

    def load_autoencoder_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.autoencoder.load_state_dict(state_dict, strict=strict)

    def load_discriminator_dict(self, state_dict: Mapping[str, Any], strict: bool = True):
        self.discriminator.load_state_dict(state_dict, strict=strict)

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
    return _net
