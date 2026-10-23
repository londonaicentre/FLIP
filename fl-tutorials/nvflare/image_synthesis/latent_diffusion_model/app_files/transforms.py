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

"""MONAI transforms for the brain-MRI cohort (MSD Task01_BrainTumour)."""

import json
from pathlib import Path

import monai.transforms as mt


def load_spatial_shape() -> list[int]:
    """Read ``spatial_shape`` from the ``config.json`` next to this file."""
    with open(Path(__file__).parent / "config.json", "r") as f:
        return list(json.load(f)["spatial_shape"])


SPATIAL_SHAPE = load_spatial_shape()

#: Non-image keys carried through the chain; ``modality_index`` is the latent diffusion condition.
MODALITY_KEYS = ("modality", "modality_index")


def get_brain_mri_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for brain-MRI training/validation.

    Args:
        is_validation (bool): When True, skip random augmentation.

    Returns:
        mt.Compose: Composed transform pipeline keyed on "image".
    """
    transforms = [
        mt.LoadImaged(keys=["image"], image_only=True),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Orientationd(keys=["image"], axcodes="RAS", labels=None),
        mt.Resized(keys=["image"], spatial_size=SPATIAL_SHAPE),
        mt.NormalizeIntensityd(keys=["image"], channel_wise=True),
    ]
    if not is_validation:
        transforms.append(
            mt.RandAffined(
                keys=["image"],
                rotate_range=(-0.05, 0.05),
                scale_range=(0.01, 0.05),
                translate_range=(-0.05, 0.05),
                prob=1.0,
                padding_mode="border",
            )
        )
    return mt.Compose(transforms)
