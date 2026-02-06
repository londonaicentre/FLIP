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

from typing import List, Tuple, Union

import monai.transforms as mt


def get_train_transforms(spatial_shape: Union[Tuple, List]):
    return mt.Compose([
        mt.LoadImaged(keys=["image"], image_only=True),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Orientationd(keys=["image"], axcodes="RAS"),
        mt.Spacingd(
            keys=["image"],
            pixdim=(1.5, 1.5, 2.0),
            mode=("bilinear"),
        ),
        mt.ResizeWithPadOrCropd(keys=["image"], spatial_size=spatial_shape),
        mt.RandAffined(
            keys=["image"],
            shear_range=(-0.1, 0.1),
            scale_range=(0.01, 0.05),
            rotate_range=(-0.05, 0.05),
            translate_range=(-0.05, 0.05),
            prob=1.0,
            padding_mode="border",
        ),
        mt.ScaleIntensityRanged(keys=["image"], a_min=-57, a_max=250, b_min=0.0, b_max=1.0, clip=True),
    ])


def get_val_transforms(spatial_shape: Union[Tuple, List]):
    return mt.Compose([
        mt.LoadImaged(keys=["image"], image_only=True),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Orientationd(keys=["image"], axcodes="RAS"),
        mt.Spacingd(
            keys=["image"],
            pixdim=(1.5, 1.5, 2.0),
            mode=("bilinear"),
        ),
        mt.ResizeWithPadOrCropd(keys=["image"], spatial_size=spatial_shape),
        mt.ScaleIntensityRanged(keys=["image"], a_min=-57, a_max=250, b_min=0.0, b_max=1.0, clip=True),
    ])
