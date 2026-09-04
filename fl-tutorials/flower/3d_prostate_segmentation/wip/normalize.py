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
#
# Copied verbatim from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/normalize.py,
# flattened into this tutorial's directory.

from typing import Optional

import nibabel as nib
import numpy as np
from monai.transforms import Transform


class NormalizeData(Transform):
    """Normalize the input image data to zero mean and unit variance"""

    def __init__(
        self, mean: Optional[float] = None, std: Optional[float] = None
    ) -> None:
        """Normalize the input image data to zero mean and unit variance

        Args:
            mean (Optional[float], optional): The mean value for normalization. Defaults to None.
            std (Optional[float], optional): The standard deviation value for normalization. Defaults to None.
        """
        self.mean = mean
        self.std = std

    def __call__(self, data: dict) -> dict:
        """Normalize the input image data to zero mean and unit variance

        Args:
            data (dict): A dictionary containing the input image and mask.

        Returns:
            dict: A dictionary containing the normalized image and mask.
        """
        img = data["image"].get_fdata()

        if self.mean is not None and self.std is not None:
            mean = self.mean
            std = self.std
        else:
            mean = np.mean(img)
            std = np.std(img)

        img = (img - mean) / std

        image_nii = nib.Nifti1Image(img, data["image"].affine)
        image_nii.header.set_zooms(data["image"].header.get_zooms())
        image_nii.header.extensions = data["image"].header.extensions

        return {"image": image_nii, "mask": data["mask"]}