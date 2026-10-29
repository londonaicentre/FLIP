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

from collections.abc import Callable, Iterator
from typing import Any

from monai.data import PatchIterd
from monai.transforms import (
    CenterSpatialCropd,
    Compose,
    NormalizeIntensityd,
    RandAxisFlipd,
    RandCoarseDropoutd,
    RandGaussianNoised,
    RandGaussianSmoothd,
    RandRotate90d,
    RandShiftIntensityd,
    RandZoomd,
    SpatialPadd,
)

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


def build_augmentations(image_std: float) -> Compose:
    """The training augmentations. Same transforms as MambaX-Net, but with our own probabilities.

    These run on the whole volume before it is split into patches (see ``client_app.py``), the same
    order MambaX-Net uses.

    Args:
        image_std: The plan's intensity std (``task.PlanGeometry.image_std``). The images are already
            divided by it, so the intensity shift is divided by it too.

    Returns:
        Compose: Operates on a `{"image", "mask"}` dict.
    """
    return Compose(
        [
            RandAxisFlipd(prob=0.1, keys=KEYS),
            RandRotate90d(prob=0.2, keys=KEYS),
            RandGaussianNoised(keys=["image"], prob=0.45),
            RandShiftIntensityd(keys=["image"], offsets=(10 / image_std, 20 / image_std), prob=0.15),
            RandZoomd(prob=0.25, min_zoom=0.8, max_zoom=1.2, keep_size=True, keys=KEYS, mode=("area", "nearest")),
            RandGaussianSmoothd(
                keys=["image"],
                sigma_x=(0.25, 1.5),
                sigma_y=(0.25, 1.5),
                sigma_z=(0.25, 1.5),
                approx="erf",
                prob=0.15,
            ),
            RandCoarseDropoutd(keys=["image"], holes=8, max_holes=15, spatial_size=(30, 30, 5), prob=0.15),
        ]
    )


def build_patch_iter(patch_size: tuple[int, int, int]) -> Callable[[dict], Iterator[tuple[dict, Any]]]:
    """Split a preprocessed volume into the plan's training patches.

    If the volume is smaller than the patch (e.g. 21 slices with a 24-slice patch), it is zero-padded
    first. Otherwise ``PatchIterd`` would return a smaller patch, and the network can't take a size
    its pooling doesn't divide. ``mode="wrap"`` fills the last partial patch so the edges are kept.

    Args:
        patch_size: The patch as (x, y, z), i.e. ``task.PlanGeometry.patch_size``.

    Returns:
        A function that yields ``(patch dict, coord)`` pairs, each patch exactly ``patch_size``.
    """
    pad = SpatialPadd(keys=KEYS, spatial_size=patch_size)
    tiles = PatchIterd(keys=KEYS, patch_size=patch_size, start_pos=(0, 0, 0), mode="wrap")

    def iterate(data: dict) -> Iterator[tuple[dict, Any]]:
        return tiles(pad(data))

    return iterate
