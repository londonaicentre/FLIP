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
#

import errno
import os
from typing import List, Optional, Type, Union

import gdown
import torch
import torch.nn as nn
from torch import Tensor


def conv3x3x3(in_planes: int, out_planes: int, stride: int = 1, dilation: int = 1) -> nn.Conv3d:
    """3x3x3 convolution with padding"""
    return nn.Conv3d(
        in_planes,
        out_planes,
        kernel_size=3,
        dilation=dilation,
        stride=stride,
        padding=dilation,
        bias=False,
    )


def conv1x1x1(in_planes: int, out_planes: int, stride: int = 1) -> nn.Conv3d:
    """1x1x1 convolution"""
    return nn.Conv3d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)


class BasicBlock(nn.Module):
    expansion: int = 1

    def __init__(
        self,
        inplanes: int,
        planes: int,
        stride: int = 1,
        downsample: Optional[nn.Module] = None,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        self.conv1 = conv3x3x3(inplanes, planes, stride=stride, dilation=dilation)
        self.bn1 = nn.BatchNorm3d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3x3(planes, planes, dilation=dilation)
        self.bn2 = nn.BatchNorm3d(planes)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation

    def forward(self, x: Tensor) -> Tensor:
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion: int = 4

    def __init__(
        self,
        inplanes: int,
        planes: int,
        stride: int = 1,
        downsample: Optional[nn.Module] = None,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        self.conv1 = conv1x1x1(inplanes, planes)
        self.bn1 = nn.BatchNorm3d(planes)
        self.conv2 = conv3x3x3(
            planes,
            planes,
            stride=stride,
            dilation=dilation,
        )
        self.bn2 = nn.BatchNorm3d(planes)
        self.conv3 = conv1x1x1(planes, planes * 4)
        self.bn3 = nn.BatchNorm3d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation

    def forward(self, x: Tensor) -> Tensor:
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):
    def __init__(
        self,
        block: Type[Union[BasicBlock, Bottleneck]],
        layers: List[int],
    ) -> None:
        super().__init__()

        self.inplanes = 64
        self.layers = layers

        self.conv1 = nn.Conv3d(1, self.inplanes, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm3d(self.inplanes)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool3d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=1, dilation=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=1, dilation=4)

    def _make_layer(
        self, block: Type[Union[BasicBlock, Bottleneck]], planes: int, blocks: int, stride: int = 1, dilation: int = 1
    ):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1x1(
                    self.inplanes,
                    planes * block.expansion,
                    stride=stride,
                ),
                nn.BatchNorm3d(planes * block.expansion),
            )

        layers = []
        layers.append(
            block(
                self.inplanes,
                planes,
                stride=stride,
                dilation=dilation,
                downsample=downsample,
            )
        )
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, dilation=dilation))

        return nn.Sequential(*layers)

    def forward(self, x: Tensor) -> Tensor:
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        return x


def download_model(url: str, filename: str, model_dir: Optional[str] = None, progress: bool = True) -> str:
    if model_dir is None:
        hub_dir = torch.hub.get_dir()
        model_dir = os.path.join(hub_dir, "medicalnet")

    try:
        os.makedirs(model_dir)
    except OSError as e:
        if e.errno == errno.EEXIST:
            # Directory already exists, ignore.
            pass
        else:
            # Unexpected OSError, re-raise.
            raise

    cached_file = os.path.join(model_dir, filename)
    if not os.path.exists(cached_file):
        gdown.download(
            url=url,
            output=cached_file,
            quiet=not progress,
        )
    return cached_file


def medicalnet_resnet10(
    model_dir: Optional[str] = None,
    filename: str = "resnet_10.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1lCEK_K5q90YaOtyfkGAjUCMrqcQZUYV0",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(BasicBlock, [1, 1, 1, 1])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet10_23datasets(
    model_dir: Optional[str] = None,
    filename: str = "resnet_10_23dataset.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1HLpyQ12SmzmCIFjMcNs4j3Ijyy79JYLk",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(BasicBlock, [1, 1, 1, 1])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet50(
    model_dir: Optional[str] = None,
    filename: str = "resnet_50.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1E7005_ZT_z6tuPpPNRvYkMBWzAJNMIIC",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(Bottleneck, [3, 4, 6, 3])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet50_23datasets(
    model_dir: Optional[str] = None,
    filename: str = "resnet_50_23dataset.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1qXyw9S5f-6N1gKECDfMroRnPZfARbqOP",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(Bottleneck, [3, 4, 6, 3])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet101(
    model_dir: Optional[str] = None,
    filename: str = "resnet_101.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1mMNQvhlaS-jmnbyqdniGNSD5aONIidKt",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(Bottleneck, [3, 4, 23, 3])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet152(
    model_dir: Optional[str] = None,
    filename: str = "resnet_152.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=1Lixxc9YsZZqAl3mnAh7PwT8c3sTXoinE",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(Bottleneck, [3, 8, 36, 3])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model


def medicalnet_resnet200(
    model_dir: Optional[str] = None,
    filename: str = "resnet_200.pth",
    progress: bool = True,
) -> ResNet:
    cached_file = download_model(
        "https://drive.google.com/uc?export=download&id=13BGtYw2fkvDSlx41gOZ5qTFhhrDB_zXr",
        filename,
        model_dir,
        progress,
    )
    model = ResNet(Bottleneck, [3, 24, 36, 3])

    # Fix checkpoints saved with DataParallel wrapper
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pretrained_state_dict = torch.load(cached_file, map_location=device)
    pretrained_state_dict = pretrained_state_dict["state_dict"]
    pretrained_state_dict = {k.replace("module.", ""): v for k, v in pretrained_state_dict.items()}
    model.load_state_dict(pretrained_state_dict)

    return model
