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
    return Compose([
        SpatialPadd(keys=KEYS, spatial_size=[*crop_size, -1]),
        CenterSpatialCropd(keys=KEYS, roi_size=[*crop_size, -1]),
        NormalizeIntensityd(keys="image", subtrahend=image_mean, divisor=image_std),
    ])


def build_augmentations() -> Compose:
    """The training augmentations, with MambaX-Net's exact set and probabilities.

    Applied to the WHOLE volume before patching (see ``client_app.py``), which is the order upstream's
    PicSegDataset used. ``Rand3DElasticd`` is deliberately absent: an earlier port had added it, it
    never ran upstream, and it would change what the model sees.

    Returns:
        Compose: Operates on a `{"image", "mask"}` dict.
    """
    return Compose([
        RandAxisFlipd(prob=0.1, keys=KEYS),
        RandRotate90d(prob=0.2, keys=KEYS),
        RandGaussianNoised(keys=["image"], prob=0.45),
        RandShiftIntensityd(keys=["image"], offsets=(10, 20), prob=0.15),
        RandZoomd(prob=0.25, min_zoom=0.8, max_zoom=1.2, keep_size=True, keys=KEYS),
        RandGaussianSmoothd(
            keys=["image"],
            sigma_x=(0.25, 1.5),
            sigma_y=(0.25, 1.5),
            sigma_z=(0.25, 1.5),
            approx="erf",
            prob=0.15,
        ),
        RandCoarseDropoutd(keys=["image"], holes=8, max_holes=15, spatial_size=(30, 30, 5), prob=0.15),
    ])


def build_patch_iter(patch_size: tuple[int, int, int]) -> Callable[[dict], Iterator[tuple[dict, Any]]]:
    """Tile a preprocessed volume into the plan's training patches — nnU-Net trains on patches, not volumes.

    A volume smaller than the patch along an axis is first zero-padded up to it, as nnU-Net pads an
    image smaller than its patch: ``PatchIterd`` would otherwise shrink the patch to the volume (a
    21-slice study under a 24-slice patch), and a patch the network's pooling cannot divide breaks
    the skip connections. Then ``mode="wrap"`` pads the last, partial tile with voxels wrapped from
    the volume's start, so every voxel is covered exactly once and the grid never drops the edge.

    Args:
        patch_size: (x, y, z) patch, the plan's ``patch_size`` reversed (``task.PlanGeometry.patch_size``).

    Returns:
        A callable yielding ``(patch dict, coord)`` pairs over a `{"image", "mask"}` dict, every patch
        exactly ``patch_size``.
    """
    pad = SpatialPadd(keys=KEYS, spatial_size=patch_size)
    tiles = PatchIterd(keys=KEYS, patch_size=patch_size, start_pos=(0, 0, 0), mode="wrap")

    def iterate(data: dict) -> Iterator[tuple[dict, Any]]:
        return tiles(pad(data))

    return iterate
