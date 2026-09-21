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

from monai.transforms import CenterSpatialCropd, Compose, NormalizeIntensityd, SpatialPadd

KEYS = ["image", "mask"]


def build_case_transform(crop_size: tuple[int, int], image_mean: float, image_std: float) -> Compose:
    """In-plane pad/crop to a fixed size, then normalize — MambaX-Net's per-case preprocessing.

    Args:
        crop_size: Target (x, y) in-plane size for "image" and "mask" — padded if smaller,
            center-cropped if larger. Depth (z) is left untouched, matching InPlaneCrop.
        image_mean: Mean subtracted from "image" (nnU-Net's foreground intensity mean). "mask" is
            never normalized.
        image_std: Standard deviation "image" is divided by after subtracting `image_mean`.

    Returns:
        Compose: Applies to a `{"image": tensor, "mask": tensor}` dict (channel-first, any spatial
        shape) and returns that same dict shape.
    """
    return Compose(
        [
            SpatialPadd(keys=KEYS, spatial_size=[*crop_size, -1]),
            CenterSpatialCropd(keys=KEYS, roi_size=[*crop_size, -1]),
            NormalizeIntensityd(keys="image", subtrahend=image_mean, divisor=image_std),
        ]
    )


if __name__ == "__main__":
    demo()
