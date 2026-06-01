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

"""Model definition for the MONAI training app.

Kept in lock-step with the evaluation tutorial's ``models.py`` (and the
flip-fl-base reference) so that the ``FL_global_model.pt`` produced by this
tutorial is a state-dict drop-in for the evaluation tutorial's ``model.pt``.
Any config change here must mirror the eval tutorial's UNet args.
"""

import torch
from monai.networks.nets import UNet
from torch import nn


class SegmentationNetwork(nn.Module):
    """Wraps a MONAI UNet that returns raw logits.

    The sliding-window inferer used for evaluation feeds logits straight in,
    and the training loss (``DiceLoss(softmax=True)``) applies its own softmax,
    so the network itself doesn't need to sigmoid/softmax internally.
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
        return self.net(x)


def get_model() -> nn.Module:
    """Create a fresh model instance.

    NOTE: this function takes no arguments — Flower's checkpoint plumbing
    instantiates the model via a zero-arg call. Bake any model config into
    the constructor above.
    """
    return SegmentationNetwork()
