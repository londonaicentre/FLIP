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

"""MONAI transforms for chest-X-ray inputs."""

import json
from pathlib import Path

import monai.transforms as mt


def load_spatial_shape() -> list[int]:
    """Read ``spatial_shape`` from the ``config.json`` next to this file."""
    with open(Path(__file__).parent / "config.json", "r") as f:
        return list(json.load(f)["spatial_shape"])


SPATIAL_SHAPE = load_spatial_shape()


def to_grayscale(image):
    """Average an ``(H, W, 3)`` RGB-stored radiograph to ``(H, W)``; grayscale passes through."""
    if image.ndim == 3 and image.shape[-1] == 3:
        return image.float().mean(dim=-1)
    return image


def get_xray_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for chest-X-ray training/validation.

    Args:
        is_validation (bool): When True, skip random augmentation.

    Returns:
        mt.Compose: Composed transform pipeline keyed on "image".
    """
    transforms = [
        mt.LoadImaged(keys=["image"], reader="PydicomReader", swap_ij=False),
        mt.Lambdad(keys=["image"], func=to_grayscale),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Resized(keys=["image"], spatial_size=SPATIAL_SHAPE),
        mt.ScaleIntensityd(keys=["image"]),
    ]
    if not is_validation:
        transforms.append(mt.RandAffined(keys=["image"], rotate_range=[-0.05, 0.05], scale_range=[0.01, 0.05]))
    return mt.Compose(transforms)
