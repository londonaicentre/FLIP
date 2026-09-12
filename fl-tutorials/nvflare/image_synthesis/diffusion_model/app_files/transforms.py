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

Taken from the ``xray_classification`` tutorial — same reader, same pinning, same chain — with one
deliberate difference: the images are resized to 256x256 rather than the classifier's 224, because
a power of two divides cleanly through every downsampling level of the autoencoder and the two
diffusion models. ``SPATIAL_SHAPE`` is exported so the trainers can shape their noise tensors from
the same constant the resize uses; ``config.json``'s ``spatial_shape`` must agree with it (pinned by
fl-tutorials/tests/test_image_synthesis_config_parity.py).

By convention a FLIP app declares its transforms here, so the inference chain can be found in one
predictable place when the model is later packaged for deployment. See
``docs/source/working-with-flip-apps/package-model-as-map.rst``.
"""

import monai.transforms as mt

#: Spatial size every image is resized to. Must equal ``config.json``'s ``spatial_shape``.
SPATIAL_SHAPE = [256, 256]


def get_xray_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for chest-X-ray training/validation.

    Args:
        is_validation (bool): When True, skip random affine augmentation so validation is
            deterministic. This is the chain to transcribe when exporting the model for inference.

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
        mt.Resized(keys=["image"], spatial_size=SPATIAL_SHAPE),
        mt.ScaleIntensityd(keys=["image"]),
    ]
    if not is_validation:
        transforms.append(mt.RandAffined(keys=["image"], rotate_range=[-0.05, 0.05], scale_range=[0.01, 0.05]))
    return mt.Compose(transforms)
