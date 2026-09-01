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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/crop.py,
# flattened into this tutorial's directory.

import nibabel as nib
import torch
from monai.data import MetaTensor
from monai.transforms import CenterSpatialCrop, Transform


class InPlaneCrop(Transform):
    """Crop the input image and mask to the target in-plane dimensions."""

    def __init__(self, x_dim: int, y_dim: int) -> None:
        """Initialize the InPlaneCrop transform.

        Args:
            x_dim (int): The target in-plane x dimension.
            y_dim (int): The target in-plane y dimension.
        """
        self.x_dim = x_dim
        self.y_dim = y_dim

        self.upscale_dim = 384

    def __call__(self, data: dict) -> dict:
        """Crop the input image and mask to the target in-plane dimensions.

        Args:
            data (dict): A dictionary containing the input image and mask.

        Returns:
            dict: A dictionary containing the cropped image and mask.
        """
        shape = data["image"].header.get_data_shape()

        if shape[1] == self.x_dim and shape[2] == self.y_dim:
            return data

        if shape[1] < self.x_dim or shape[2] < self.y_dim:
            image = data["image"].get_fdata()
            mask = data["mask"].get_fdata()

            image = image[None, ...]
            mask = mask[None, ...]

            # Calculate padding sizes
            pad_size_x = max(0, self.x_dim - shape[1])
            pad_size_y = max(0, self.y_dim - shape[2])

            # Calculate left/top and right/bottom padding sizes
            pad_left = pad_size_x // 2
            pad_right = pad_size_x - pad_left
            pad_top = pad_size_y // 2
            pad_bottom = pad_size_y - pad_top

            # Pad the image and mask
            # torch.nn.functional.pad pads from LAST dimension backwards
            # For shape (C, X, Y, D): pad order is (D_front, D_back, Y_front, Y_back, X_front, X_back)
            image = torch.nn.functional.pad(
                torch.from_numpy(image.copy()),
                (0, 0, pad_top, pad_bottom, pad_left, pad_right),
            )
            mask = torch.nn.functional.pad(
                torch.from_numpy(mask.copy()).float(),
                (0, 0, pad_top, pad_bottom, pad_left, pad_right),
            )
            # remove the batch dimension
            image = image.squeeze(0).numpy()
            mask = mask.squeeze(0).numpy()  # Remove .half() to avoid float16 dtype issues
        else:
            image, mask = data["image"].get_fdata(), data["mask"].get_fdata()

        # Convert to MetaTensor to preserve affine information during cropping
        image_meta = MetaTensor(image, affine=data["image"].affine)
        mask_meta = MetaTensor(mask, affine=data["mask"].affine)

        # Apply CenterSpatialCrop - this correctly updates the affine matrix
        image_cropped = CenterSpatialCrop([self.x_dim, self.y_dim, -1])(image_meta)
        mask_cropped = CenterSpatialCrop([self.x_dim, self.y_dim, -1])(mask_meta)

        # Create Nifti with the corrected affine from the cropped MetaTensors
        image_nii = nib.Nifti1Image(image_cropped.cpu().numpy(), image_cropped.affine.numpy())
        image_nii.header.set_zooms(data["image"].header.get_zooms())
        image_nii.header.extensions = data["image"].header.extensions

        mask_nii = nib.Nifti1Image(mask_cropped.cpu().numpy(), mask_cropped.affine.numpy())
        mask_nii.header.set_zooms(data["mask"].header.get_zooms())
        mask_nii.header.extensions = data["mask"].header.extensions

        return {"image": image_nii, "mask": mask_nii}
