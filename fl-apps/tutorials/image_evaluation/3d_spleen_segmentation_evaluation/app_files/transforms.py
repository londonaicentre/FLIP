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

import torch
from monai.inferers import SlidingWindowInferer
from monai.transforms import (
    Compose,
    CropForegroundd,
    EnsureChannelFirstd,
    EnsureTyped,
    LoadImaged,
    Orientationd,
    ScaleIntensityRanged,
    Spacingd,
)


def get_eval_transforms():
    """Return the MONAI evaluation transforms (same as trainer but with no augmentation)."""
    return Compose([
        LoadImaged(keys=["image", "label"]),
        EnsureChannelFirstd(keys=["image", "label"]),
        Orientationd(keys=["image", "label"], axcodes="RAS"),
        ScaleIntensityRanged(
            keys=["image"],
            a_min=-57,
            a_max=250,
            b_min=0.0,
            b_max=1.0,
            clip=True,
        ),
        CropForegroundd(keys=["image", "label"], source_key="image", allow_smaller=True),
        Spacingd(keys=["image", "label"], pixdim=(1.5, 1.5, 2.0), mode=("bilinear", "nearest")),
        EnsureTyped(keys=["image", "label"]),
    ])


def get_sliding_window_inferer(sw_device: torch.device):
    """Return a MONAI SlidingWindowInferer for validation and inference."""
    return SlidingWindowInferer(
        sw_batch_size=1,
        roi_size=(96, 96, 96),
        sw_device=sw_device,
        progress=False,
    )
