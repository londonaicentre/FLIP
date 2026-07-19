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

"""MONAI transforms for chest-X-ray inputs.

By convention a FLIP app declares its transforms here, so the inference chain can be found in one
predictable place when the model is later packaged for deployment. See
``docs/source/working-with-flip-apps/package-model-as-map.rst``.
"""

import monai.transforms as mt


def get_xray_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for chest-X-ray training/validation.

    Args:
        is_validation (bool): When True, skip random affine augmentation so validation is
            deterministic. This is the chain to transcribe when exporting the model for inference.

    Returns:
        mt.Compose: Composed transform pipeline keyed on "image".
    """
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
