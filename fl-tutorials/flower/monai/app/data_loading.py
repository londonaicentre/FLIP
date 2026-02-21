# Copyright (c) 2026 Flower Labs GmbH
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

"""Spleen Segmentation: Data loading and transform functions (training-only)."""

import logging
from typing import Dict, List

from monai.transforms import (
    Compose,
    CropForegroundd,
    EnsureChannelFirstd,
    EnsureTyped,
    LoadImaged,
    Orientationd,
    RandCropByPosNegLabeld,
    ResizeWithPadOrCropd,
    ScaleIntensityRanged,
    Spacingd,
)

from app.get_data import get_data, get_images_from_accession_id

# Logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_train_transforms() -> Compose:
    """Get transforms for training data."""
    return Compose([
        LoadImaged(keys=["image", "label"]),
        EnsureChannelFirstd(keys=["image", "label"]),
        Orientationd(keys=["image", "label"], axcodes="RAS"),
        Spacingd(
            keys=["image", "label"],
            pixdim=(1.5, 1.5, 2.0),
            mode=("bilinear", "nearest"),
        ),
        ResizeWithPadOrCropd(keys=["image", "label"], spatial_size=None),
        ScaleIntensityRanged(
            keys=["image"],
            a_min=-57,
            a_max=164,
            b_min=0.0,
            b_max=1.0,
            clip=True,
        ),
        CropForegroundd(keys=["image", "label"], source_key="image"),
        RandCropByPosNegLabeld(
            keys=["image", "label"],
            label_key="label",
            spatial_size=(96, 96, 96),
            pos=1,
            neg=1,
            num_samples=4,
            image_key="image",
            image_threshold=0,
        ),
        EnsureTyped(keys=["image", "label"]),
    ])


def get_datalist(val_split: float = 0.25, test_split: float = 0.0, is_test: bool = False) -> List[Dict[str, str]]:
    """Get data list for training, validation, or testing."""
    df_accession_ids = get_data()
    accession_ids = df_accession_ids["accession_id"].tolist()
    data_dict: List[Dict[str, str]] = []
    for accession_id in accession_ids:
        image_path, label_path = get_images_from_accession_id(accession_id)
        if image_path and label_path:
            data_dict.append({
                "image": str(image_path),
                "label": str(label_path),
            })
        else:
            logger.warning("Skipping accession_id %s due to missing data.", accession_id)

    n_images_val = int(val_split * len(data_dict))
    n_images_test = int(test_split * len(data_dict))
    n_images_train = len(data_dict) - n_images_val - n_images_test

    train_datalist = data_dict[:n_images_train]
    val_datalist = data_dict[n_images_train : n_images_train + n_images_val]
    test_datalist = data_dict[n_images_train + n_images_val :]

    if is_test:
        print(f"Found {len(test_datalist)} test samples.")
        return list(test_datalist)

    print(f"Found {len(train_datalist)} training samples and {len(val_datalist)} validation samples.")
    return list(train_datalist), list(val_datalist)
