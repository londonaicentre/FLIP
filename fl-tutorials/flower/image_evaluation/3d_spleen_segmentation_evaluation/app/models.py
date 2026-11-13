# Copyright (c) 2026 Flower Labs GmbH
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
#

"""Model definition for the MONAI evaluation app."""

import torch
from monai.networks.nets import UNet
from torch import nn


class SegmentationNetwork(nn.Module):
    """
    Wraps a MONAI UNet allowing the choice of returning the logits or sigmoided logits. This is useful
    because we train on patches, but evaluate on full images using a sliding window approach. We need to return
    logits for the sliding window approach, but sigmoided logits for the patch training approach.
    """

    def __init__(self, num_classes: int = 1):
        super().__init__()

        self.net = UNet(
            spatial_dims=3,
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


def get_model() -> nn.Module:
    """Create a fresh, uninitialised model instance for evaluation.

    The evaluation app evaluates a single model; the checkpoint loaded into it
    is named by the ``checkpoint`` run-config value.
    """
    return SegmentationNetwork()
