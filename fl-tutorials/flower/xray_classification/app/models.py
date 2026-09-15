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

from monai.networks.nets.densenet import DenseNet121
from torch import nn


def get_model() -> nn.Module:
    """Return a fresh chest-X-ray DenseNet for FLIP.

    The base bundle's ``server_app.py`` calls this to seed the initial weights;
    the per-tutorial signature must stay zero-arg.
    """
    return DenseNet()


class DenseNet(nn.Module):
    """Wraps MONAI's DenseNet121 for 2D chest-X-ray multi-label classification.

    Trained from scratch, deliberately. ``pretrained=True`` downloaded torchvision's ImageNet
    checkpoint at construction time — on the FL server and on every client — and an FL app must
    never fetch anything at run time (FLIP#1206): the FL server on a platform-managed estate and a
    trust host behind an NHS firewall have no internet route, and a run-time download bypasses the
    scanned upload path a reviewer approved. It also bought little here: with this 1-channel,
    128-feature stem only ~30% of the tensors matched the ImageNet shapes, and the tutorial reaches
    the same validation F1 in the same number of epochs without them. An app that does need
    pretrained weights ships them as an uploaded file — see the latent-diffusion tutorial.
    """

    def __init__(self):
        super().__init__()
        self.net = DenseNet121(spatial_dims=2, in_channels=1, out_channels=2, init_features=128, pretrained=False)

    def forward(self, x):
        return self.net(x)
