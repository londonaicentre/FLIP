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

# Description: Defines the SegmentationNetwork (MONAI UNet) used for spleen segmentation evaluation.

import json
from pathlib import Path

import torch
from monai.networks.nets import UNet
from torch import nn


def load_net_config() -> dict:
    """Load the network params (e.g. spatial_dims) from config.json next to this module."""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path) as f:
        config = json.load(f)
    return config.get("net_config", {})


class SegmentationNetwork(nn.Module):
    """MONAI UNet for spleen segmentation. Returns logits (sliding-window inference applies softmax)."""

    def __init__(self, num_classes: int = 1):
        super().__init__()
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

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def get_model() -> nn.Module:
    """Return the model architecture. Referenced by the recipe's persistor (``models.get_model``) and by
    the evaluator to receive the server-provided weights."""
    return SegmentationNetwork()
