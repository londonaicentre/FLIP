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


def load_config() -> dict:
    """Read the ``config.json`` next to this file."""
    with open(Path(__file__).parent / "config.json", "r") as f:
        return json.load(f)


#: The grid every volume is cropped or padded to, after resampling to ``VOXEL_SPACING`` millimetres.
#: Task01's scans are 240x240x155 mm; at 2 mm, 96x112x80 holds every brain in it whole (the largest is
#: 160x187x149 mm, and brains sit up to 20 mm off-centre), where a 96^3 grid would cut 95 of 484.
SPATIAL_SHAPE = list(load_config()["spatial_shape"])
VOXEL_SPACING = list(load_config()["voxel_spacing"])


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
        # A fixed physical voxel size, then a centre crop or pad, as MONAI's 3-D latent diffusion
        # tutorial does, so anatomy keeps its proportions. Resizing straight to the grid would scale
        # each axis by a different factor.
        mt.Spacingd(keys=["image"], pixdim=VOXEL_SPACING, mode="bilinear"),
        mt.ResizeWithPadOrCropd(keys=["image"], spatial_size=SPATIAL_SHAPE),
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
