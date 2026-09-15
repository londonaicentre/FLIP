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
# Copied from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/dataset/collate/custom_collate.py

from typing import Any, List, Tuple

import torch
from einops import rearrange
from torch.nn.utils.rnn import pad_sequence


def patch_collate_fingerprint(
    batch: List[dict],
) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
    """Collate function for fingerprinting patches.

    Args:
        batch: List of dictionaries containing image and mask tensors.

    Returns:
        A tuple containing two lists - images and masks.
    """
    imgs = [item["image"] for item in batch]
    masks = [item["mask"] for item in batch]
    return imgs, masks


def patch_collate_infer(
    batch: List[tuple],
) -> Tuple[
    List[str],
    torch.Tensor,
    torch.Tensor,
    List[Any],
    List[Any],
    List[tuple],
    List[tuple],
    List[Any],
    List[Any],
    List[tuple],
    List[tuple],
    List[Any],
    List[int],
]:
    """Collate function for inference patches.

    Args:
        batch: List of tuples containing batch data.

    Returns:
        A tuple containing names, stacked image patches, stacked mask patches,
        image coordinates, mask coordinates, image shapes, mask shapes,
        image nii objects, mask nii objects, original shapes, original spacings,
        original affines, and patches_per_image (number of patches for each image).
    """
    names = [item[0] for item in batch]
    patches = [item[1] for item in batch]
    img_shapes = [item[2] for item in batch]
    mask_shapes = [item[3] for item in batch]
    img_nii = [item[4] for item in batch]
    mask_nii = [item[5] for item in batch]

    # Handle original shapes, spacings, and affines if available (for inference mode)
    if len(batch[0]) == 9:
        original_shapes = [item[6] for item in batch]
        original_spacings = [item[7] for item in batch]
        original_orientations = [item[8] for item in batch]
    elif len(batch[0]) == 8:
        original_shapes = [item[6] for item in batch]
        original_spacings = [item[7] for item in batch]
        original_orientations = [None for _ in batch]
    else:
        # For backward compatibility with train_seg_maps mode or older data (6 elements)
        original_shapes = [None for _ in batch]
        original_spacings = [None for _ in batch]
        original_orientations = [None for _ in batch]

    img_patches = []
    mask_patches = []
    patches_per_image = []  # Track number of patches for each image
    for sublist in patches:
        patches_per_image.append(len(sublist))
        for item in sublist:
            # Handle both numpy arrays and tensors
            if isinstance(item[0], torch.Tensor):
                img_patches.append(item[0] if item[0].dtype == torch.float32 else item[0].float())
            else:
                img_patches.append(torch.from_numpy(item[0]).float())

            if isinstance(item[1], torch.Tensor):
                mask_patches.append(item[1] if item[1].dtype == torch.float32 else item[1].float())
            else:
                mask_patches.append(torch.from_numpy(item[1]).float())

    img_coords = [item[2] for sublist in patches for item in sublist if item[2] is not None]
    mask_coords = [item[3] for sublist in patches for item in sublist if item[3] is not None]

    return (
        names,
        torch.stack(img_patches),
        torch.stack(mask_patches),
        img_coords,
        mask_coords,
        img_shapes,
        mask_shapes,
        img_nii,
        mask_nii,
        original_shapes,
        original_spacings,
        original_orientations,
        patches_per_image,
    )


def patch_collate_seg(
    batch: List[list],
) -> Tuple[torch.Tensor, torch.Tensor, List[Any], List[Any]]:
    """Collate function for segmentation patches.

    Args:
        batch: List of lists containing patch data.

    Returns:
        A tuple containing stacked image patches, stacked mask patches,
        image coordinates, and mask coordinates.
    """
    img_patches = []
    mask_patches = []

    for sublist in batch:
        for item in sublist:
            # Handle both numpy arrays and tensors
            if isinstance(item[0], torch.Tensor):
                img_patches.append(item[0] if item[0].dtype == torch.float32 else item[0].float())
            else:
                img_patches.append(torch.from_numpy(item[0]).float())

            if isinstance(item[1], torch.Tensor):
                mask_patches.append(item[1] if item[1].dtype == torch.float32 else item[1].float())
            else:
                mask_patches.append(torch.from_numpy(item[1]).float())

    img_coords = [item[2] for sublist in batch for item in sublist if item[2] is not None]
    mask_coords = [item[3] for sublist in batch for item in sublist if item[3] is not None]
    return torch.stack(img_patches), torch.stack(mask_patches), img_coords, mask_coords
