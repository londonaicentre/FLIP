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

# Description: This file defines the SegmentationNetwork class with a UNet model for segmentation.

import json
from pathlib import Path

import torch
from monai.networks.nets import UNet
from torch import nn


# Here is where we can load the config file with network params if necessary, for example:
def load_net_config():
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r") as f:
        config = json.load(f)
    net_config = config.get("net_config", {})
    print(f"Loaded network config: {net_config}")
    return net_config


class SegmentationNetwork(nn.Module):
    """
    Wraps a MONAI BasicUNet allowing the choice of returning the logits or sigmoided logits. This is useful
    because we train on patches, but evaluate on full images using a sliding window approach. We need to return
    logits for the sliding window approach, but sigmoided logits for the patch training approach.
    """

    def __init__(self, num_classes: int = 1):
        super().__init__()

        # For example
        net_config = load_net_config()

        self.net = UNet(
            spatial_dims=net_config["spatial_dims"],
            in_channels=1,
            out_channels=num_classes + 1,
            num_res_units=2,
            norm="batch",
            channels=(16, 32, 64, 128, 256),
            strides=(2, 2, 2, 2),
        )

    def forward(self, x: torch.Tensor):
        logits = self.net(x)
        return logits


_net = SegmentationNetwork()


def get_model() -> nn.Module:
    """
    Returns the model defined in this file.
    NOTE: This function needs to exist and cannot take any input arguments. If you would like to parameterize the
    configuration of your model, for example loaded from a config file, do it when instantiating the model above.
    """
    return _net
