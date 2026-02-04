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

# Centralised file to obtain trainable labels from dataframe ones

from typing import List, Sequence

import monai.transforms as mt
import torch
from pandas import Series
from pydantic import BaseModel


class Lesion(BaseModel):
    id: int
    lesion: str


class LesionDict(BaseModel):
    items: Sequence[Lesion]

    def contains(self, element_value: str) -> bool:
        """Check if a given string matches any lesion name."""
        return any(item.lesion == element_value for item in self.items)

    def get_lesion_list(self) -> List[str]:
        """Return a list of all lesion names."""
        return [item.lesion for item in self.items]


def get_labels_from_radiology_row(
    radiology_row: Series,
    lesions: LesionDict,
    value_to_numerical: dict,
    normal_label: str = "Lungs in normal arrangement",
) -> dict:
    """Extracts the formatted output dictionary of labels for the classification.

    Args:
        radiology_row (Series): DataFrame row.
        lesions (LesionDict): dictionary of lesions that will take part on the classification.
        value_to_numerical (dict): mapping from 0 to the value of 0 in the dataframe and of 1 to the value of 1
        normal_label (str, optional): normal label (negative finding, overrides every value to zero).
        Defaults to "Lungs in normal arrangement".

    Returns:
        dict: _description_
    """
    out_dict = {}
    for lesion in lesions.items:
        lesion_name = lesion.lesion
        if normal_label in radiology_row.keys() and radiology_row[normal_label] == value_to_numerical[1]:
            out_dict[lesion_name] = 0
        elif lesion_name in radiology_row.keys():
            if radiology_row[lesion_name] == value_to_numerical[1]:
                out_dict[lesion_name] = 1
            elif radiology_row[lesion_name] == value_to_numerical[0]:
                out_dict[lesion_name] = 0
            else:
                out_dict[lesion_name] = -1
    return out_dict


def get_lesion_label(in_batch: dict, lesions: LesionDict):
    out_tensor = []
    out_tensor = [in_batch[les.lesion] for les in sorted(lesions.items, key=lambda x: x.id)]
    return torch.stack(out_tensor, dim=1).float()


def get_xray_transforms(is_validation: bool = False):
    transforms = [
        mt.LoadImaged(keys=["image"]),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Resized(keys=["image"], spatial_size=[224, 224]),
        mt.Rotate90d(keys=["image"], k=-1),
        mt.ScaleIntensityd(keys=["image"]),
    ]
    if not is_validation:
        transforms.append(mt.RandAffined(keys=["image"], rotate_range=[-0.05, 0.05], scale_range=[0.01, 0.05]))
    return mt.Compose(transforms)


def validate_lesions(in_lesions: list, lesions: LesionDict):
    """
    Given a LesionsDict, verifies whether the the input lesions are valid and contained in LesionDict.
    """

    return [les for les in in_lesions if lesions.contains(les)]
