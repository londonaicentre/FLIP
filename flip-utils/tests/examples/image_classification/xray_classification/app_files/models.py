# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from monai.networks.nets.densenet import DenseNet121
from torch import nn


# -------------------------------------------------
# Function to store model path for FLIP
def get_model():
    return DenseNet()


# -------------------------------------------------


class DenseNet(nn.Module):
    """
    Calls the Densenet121 module.
    """

    def __init__(self):
        super().__init__()
        self.net = DenseNet121(spatial_dims=2, in_channels=1, out_channels=2, init_features=128, pretrained=True)

    def forward(self, x):
        return self.net(x)
