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

import monai.transforms as mt


def get_xray_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for chest-X-ray training/validation.

    Args:
        is_validation (bool): When True, skip random affine augmentation so
            validation is deterministic.

    Returns:
        mt.Compose: Composed transform pipeline keyed on "image".
    """
    transforms = [
        # The reader is pinned rather than left to MONAI's auto-detection. LoadImaged tries its
        # registered readers last-registered-first, so which one wins depends on which optional
        # backends happen to be installed: adding `itk` to the environment promotes ITKReader and
        # silently changes the array's axis order. PydicomReader(swap_ij=False) returns the pixel
        # array exactly as DICOM PixelData stores it, indexed (row, column). MONAI's default
        # swap_ij=True returns it transposed, i.e. the model is fed sideways radiographs, and no
        # rotation or flip undoes a transpose: a Rotate90d(k=-1) leaves the radiograph upright but
        # mirrored, which looks entirely correct and silently swaps the patient's left and right.
        # fl-tutorials/tests/ pins this against the raw PixelData for every app on this path.
        mt.LoadImaged(keys=["image"], reader="PydicomReader", swap_ij=False),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Resized(keys=["image"], spatial_size=[224, 224]),
        mt.ScaleIntensityd(keys=["image"]),
    ]
    if not is_validation:
        transforms.append(mt.RandAffined(keys=["image"], rotate_range=[-0.05, 0.05], scale_range=[0.01, 0.05]))
    return mt.Compose(transforms)
