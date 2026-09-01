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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/resample.py,
# flattened into this tutorial's directory.

import json
from typing import Any, Dict, Tuple

import nibabel as nib
import numpy as np
import torch
from monai.data import MetaTensor
from monai.transforms import CenterSpatialCrop, Resize, Spacing, Transform
from nibabel import Nifti1Image


def set_resampling_metadata(
    nii_image: nib.Nifti1Image,
    original_shape: Tuple[int, ...],
    resampled_shape: Tuple[int, ...],
    original_spacing: Tuple[float, ...],
    target_spacing: Tuple[float, ...],
    original_affine: np.ndarray = None,
) -> nib.Nifti1Image:
    """
    Add resampling metadata to NIfTI header as an extension.

    Args:
        nii_image: NIfTI image to add metadata to
        original_shape: Original image shape before resampling
        resampled_shape: Image shape after resampling
        original_spacing: Original voxel spacing
        target_spacing: Target voxel spacing after resampling
        original_affine: Original affine matrix (optional)

    Returns:
        NIfTI image with resampling metadata in header extension
    """
    # Create metadata dictionary
    resampling_metadata = {
        "original_shape": [int(x) for x in original_shape],
        "resampled_shape": [int(x) for x in resampled_shape],
        "original_spacing": [float(x) for x in original_spacing],
        "target_spacing": [float(x) for x in target_spacing],
        # "original_header": nii_image.header(),
        "resampling_timestamp": str(np.datetime64("now")),
        "original_origin": original_affine[:3, 3].tolist(),
        "original_affine": original_affine.tolist(),
        "mambax_net_version": "1.0", 
    }

    metadata_json = json.dumps(resampling_metadata, indent=2)
    metadata_bytes = metadata_json.encode("utf-8")

    extension = nib.nifti1.Nifti1Extension(40, metadata_bytes)

    new_header = nii_image.header.copy()
    new_header.extensions.clear()  # Remove existing extensions if any
    new_header.extensions.append(extension)

    return nib.Nifti1Image(nii_image.get_fdata(), nii_image.affine, new_header)


def get_resampling_metadata(nii_image: nib.Nifti1Image) -> Dict[str, Any]:
    """
    Retrieve resampling metadata from NIfTI header extension.

    Args:
        nii_image: NIfTI image to extract metadata from

    Returns:
        Dictionary containing resampling metadata, or empty dict if not found
    """
    for extension in nii_image.header.extensions:
        if extension.get_code() == 40:  # Our custom extension code
            try:
                metadata_json = extension.get_content().decode("utf-8")
                return json.loads(metadata_json)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
    return {}


def has_resampling_metadata(nii_image: nib.Nifti1Image) -> bool:
    """
    Check if NIfTI image has resampling metadata in header.

    Args:
        nii_image: NIfTI image to check

    Returns:
        True if resampling metadata is present, False otherwise
    """
    metadata = get_resampling_metadata(nii_image)
    return bool(metadata and "original_shape" in metadata and "resampled_shape" in metadata)


def resample_mask_to_original_space(
    current_nii: nib.Nifti1Image,
    interpolation: str = "nearest",
) -> nib.Nifti1Image:
    """Resample the input mask to the original space.

    Args:
        current_nii (nib.Nifti1Image): The current NIfTI image to resample.
        interpolation (str, optional): The interpolation method to use. Defaults to "nearest".

    Returns:
        nib.Nifti1Image: The resampled NIfTI image.
    """
    if has_resampling_metadata(current_nii):
        metadata = get_resampling_metadata(current_nii)

        tensor = MetaTensor(current_nii.get_fdata().copy(), affine=current_nii.affine)
        current_shape = tensor.shape
        resampled_shape = metadata["resampled_shape"]
        original_shape = metadata["original_shape"]
        original_spacing = metadata["original_spacing"]
        original_affine = np.array(metadata["original_affine"])

        if len(resampled_shape) == 4:
            # drop the channel dimension
            resampled_shape = resampled_shape[1:]

        if len(original_shape) == 4:
            original_shape = original_shape[1:]

        assert len(current_shape) == 3, f"Expected 3D tensor, got {len(current_shape)}D"
        assert len(resampled_shape) == 3, f"Expected 3D resampled shape, got {len(resampled_shape)}D"

        if current_shape[1] > resampled_shape[1]:  # Image was padded, so we need to crop it
            cropper = CenterSpatialCrop(roi_size=resampled_shape[1:])
            tensor = cropper(tensor)
        elif current_shape[1] < resampled_shape[1]:  # Image was cropped, so we need to pad it

            pad_size_x = resampled_shape[0] - current_shape[0]
            pad_size_y = resampled_shape[1] - current_shape[1]
            pad_left, pad_right = pad_size_x // 2, pad_size_x - (pad_size_x // 2)
            pad_top, pad_bottom = pad_size_y // 2, pad_size_y - (pad_size_y // 2)

            # tensor is 3D here (asserted above, no channel dim). F.pad args apply from the
            # last dim backward: dim2 gets (0, 0), dim1 gets pad_top/bottom, dim0 gets pad_left/right.
            padded_tensor = torch.nn.functional.pad(
                tensor,
                (0, 0, pad_top, pad_bottom, pad_left, pad_right),
            )
            tensor = MetaTensor(padded_tensor, affine=tensor.affine)

        spatial_shape = original_shape[1:] if len(original_shape) == 4 else original_shape

        resampler = Resize(
            spatial_size=spatial_shape,
            mode=interpolation,
        )
        if tensor.dim() == 3:
            tensor = tensor.unsqueeze(0)

        final_image_tensor = resampler(tensor)
        final_nii = Nifti1Image(final_image_tensor.squeeze(0).cpu().numpy(), original_affine, current_nii.header)
        final_nii.header.set_zooms(original_spacing)
    else:
        final_nii = current_nii

    return final_nii


class MONAIResample(Transform):
    """Resample the input image to the target zooms"""

    def __init__(
        self,
        target_zooms: Tuple[float, float, float] = (1.0, 1.0, 1.0),
        boundary_mode: str = "nearest",
        skip_mask_preprocess: bool = False,
    ):
        """Initialize the MONAIResample transform.

        Args:
            target_zooms (Tuple[float, float, float], optional): The target zooms to resample to. Defaults to (1.0, 1.0, 1.0).
            boundary_mode (str, optional): The boundary mode to use for resampling. Defaults to "nearest".
        """
        self.target_x_zoom = target_zooms[0]
        self.target_y_zoom = target_zooms[1]
        self.target_z_zoom = target_zooms[2]
        self.boundary_mode = boundary_mode
        self.skip_mask_preprocess = skip_mask_preprocess

    def __call__(self, data: dict) -> dict:
        """Resample the input image and mask to the target zooms.

        Args:
            data (dict): A dictionary containing the input image and mask.

        Returns:
            dict: A dictionary containing the resampled image and mask.
        """
        # Extract spacing correctly from the image header
        image_zooms = data["image"].header.get_zooms()
        if len(image_zooms) >= 3:
            zoom = np.array(image_zooms[1:])  # Take  (X, Y, Z)
        else:
            zoom = np.array(image_zooms)

        original_shape = data["image"].shape

        if self.target_x_zoom is None:
            self.target_x_zoom = zoom[0]
        if self.target_y_zoom is None:
            self.target_y_zoom = zoom[1]
        if self.target_z_zoom is None:
            self.target_z_zoom = zoom[2]

        target_zooms = np.array([self.target_x_zoom, self.target_y_zoom, self.target_z_zoom])

        if not np.array_equal(zoom, target_zooms):
            img_resampler = Spacing(
                pixdim=target_zooms,
                mode=self.boundary_mode,
                dtype=data["image"].get_fdata().dtype,
            )

            image = img_resampler(
                MetaTensor(
                    data["image"].get_fdata().copy(),
                    affine=data["image"].affine.copy(),
                )
            )

            if self.skip_mask_preprocess:
                # Keep original mask unchanged
                mask = data["mask"]
            else:
                mask_resampler = Spacing(
                    pixdim=target_zooms,
                    mode=self.boundary_mode,
                    dtype=data["mask"].get_fdata().dtype,
                )
                mask = mask_resampler(
                    MetaTensor(
                        data["mask"].get_fdata().copy(),
                        affine=data["mask"].affine.copy(),
                    )
                )

            image_nii = nib.Nifti1Image(image.cpu().numpy(), image.affine, data["image"].header)

            # Extract spatial spacing from the updated affine matrix
            # Affine diagonal: [x_spacing, y_spacing, z_spacing, 1]
            # Header zooms should be: (channel_zoom, x_spacing, y_spacing, z_spacing)
            spatial_spacing = np.abs(np.diag(image.affine)[:3])  # [X, Y, Z]
            correct_zooms = (1.0,) + tuple(spatial_spacing)  # (channel, X, Y, Z)
            image_nii.header.set_zooms(correct_zooms)
            image_nii.header.set_data_shape(image.shape)

            if self.skip_mask_preprocess:
                # Use original mask data but create a new NIfTI to avoid modifying the original
                mask_nii = nib.Nifti1Image(data["mask"].get_fdata(), data["mask"].affine, data["mask"].header.copy())
            else:
                # Create new header from original to avoid modifying the source
                new_header = data["mask"].header.copy()
                mask_nii = nib.Nifti1Image(mask.cpu().numpy(), mask.affine, new_header)
                # For mask, use the same spatial spacing as image since they were resampled together
                mask_nii.header.set_zooms(correct_zooms)
                mask_nii.header.set_data_shape(mask.shape)
        else:
            # no resampling needed, just copy original
            image = data["image"]
            mask = data["mask"]
            image_nii = nib.Nifti1Image(image.get_fdata(), image.affine, image.header.copy())
            mask_nii = nib.Nifti1Image(mask.get_fdata(), mask.affine, mask.header.copy())

        resampled_shape = tuple(image.shape)
        original_affine = data["image"].affine.copy()
        image_nii = set_resampling_metadata(
            image_nii,
            original_shape=original_shape,
            resampled_shape=resampled_shape,
            original_spacing=tuple(zoom),
            target_spacing=tuple(target_zooms),
            original_affine=original_affine,
        )

        mask_nii = set_resampling_metadata(
            mask_nii,
            original_shape=original_shape,
            resampled_shape=resampled_shape,
            original_spacing=tuple(zoom),
            target_spacing=tuple(target_zooms),
            original_affine=original_affine,
        )

        return {"image": image_nii, "mask": mask_nii}


class PadDepthDivisible(Transform):
    """Pad the depth dimension to be divisible by a specified factor.

    This is necessary for UNet-style architectures that downsample/upsample
    the depth dimension with factors of 2. If the depth is not divisible,
    the skip connection concatenation will fail.
    """

    def __init__(self, divisor: int = 16, pad_mode: str = "constant", pad_value: float = 0.0):
        """Initialize the PadDepthDivisible transform.

        Args:
            divisor: The depth must be divisible by this value. Default 16 matches
                    the typical nnUNet patch size for depth.
            pad_mode: Padding mode - 'constant', 'reflect', 'replicate', etc.
            pad_value: Value to use for constant padding.
        """
        self.divisor = divisor
        self.pad_mode = pad_mode
        self.pad_value = pad_value

    def __call__(self, data: dict) -> dict:
        """Pad depth dimension to be divisible by the specified factor.

        Args:
            data: Dictionary containing 'image' and 'mask' NIfTI images.
                  Shape is expected to be (C, H, W, D) where D is depth.

        Returns:
            Dictionary with padded 'image' and 'mask'.
        """
        image_nii = data["image"]
        mask_nii = data["mask"]

        image = image_nii.get_fdata()
        mask = mask_nii.get_fdata()

        # Get current depth (last dimension after channel)
        # Shape is (C, H, W, D) for image and (C, H, W, D) for mask
        current_depth = image.shape[-1]

        # Calculate padding needed
        remainder = current_depth % self.divisor
        if remainder == 0:
            return data

        pad_amount = self.divisor - remainder

        # Pad symmetrically (half on each side, with extra on the end if odd)
        pad_before = pad_amount // 2
        pad_after = pad_amount - pad_before

        # Create padding specification for numpy: ((before, after), ...) for each dim
        # We only pad the last dimension (depth)
        image_pad_width = [(0, 0)] * (image.ndim - 1) + [(pad_before, pad_after)]
        mask_pad_width = [(0, 0)] * (mask.ndim - 1) + [(pad_before, pad_after)]

        # Pad the arrays
        if self.pad_mode == "constant":
            image_padded = np.pad(image, image_pad_width, mode="constant", constant_values=self.pad_value)
            mask_padded = np.pad(mask, mask_pad_width, mode="constant", constant_values=0)
        else:
            image_padded = np.pad(image, image_pad_width, mode=self.pad_mode)
            mask_padded = np.pad(mask, mask_pad_width, mode=self.pad_mode)

        # Create new NIfTI images with padded data
        image_nii_padded = nib.Nifti1Image(image_padded, image_nii.affine, image_nii.header)
        image_nii_padded.header.set_data_shape(image_padded.shape)

        mask_nii_padded = nib.Nifti1Image(mask_padded, mask_nii.affine, mask_nii.header)
        mask_nii_padded.header.set_data_shape(mask_padded.shape)

        # Store padding info in header extension for later unpadding
        # We'll add this to existing metadata if present, or create new metadata
        for nii in [image_nii_padded, mask_nii_padded]:
            existing_metadata = get_resampling_metadata(nii)
            if existing_metadata:
                existing_metadata["depth_pad_before"] = pad_before
                existing_metadata["depth_pad_after"] = pad_after
                existing_metadata["original_depth"] = current_depth
                metadata_to_save = existing_metadata
            else:
                # Create new metadata with padding info
                metadata_to_save = {
                    "depth_pad_before": pad_before,
                    "depth_pad_after": pad_after,
                    "original_depth": current_depth,
                }

            # Update/create the extension
            metadata_json = json.dumps(metadata_to_save, indent=2)
            metadata_bytes = metadata_json.encode("utf-8")
            extension = nib.nifti1.Nifti1Extension(40, metadata_bytes)

            nii.header.extensions.clear()
            nii.header.extensions.append(extension)

        return {"image": image_nii_padded, "mask": mask_nii_padded}


def unpad_depth(array: np.ndarray, metadata: Dict[str, Any]) -> np.ndarray:
    """Remove depth padding from an array using stored metadata.

    Args:
        array: Array to unpad. Can be 3D (H, W, D) or 4D (C, H, W, D).
               Depth is assumed to be the last dimension.
        metadata: Dictionary containing 'depth_pad_before' and 'depth_pad_after' keys.

    Returns:
        Array with padding removed from depth dimension.
    """
    pad_before = metadata.get("depth_pad_before", 0)
    pad_after = metadata.get("depth_pad_after", 0)

    if pad_before == 0 and pad_after == 0:
        return array

    # Calculate the slice for the depth dimension
    depth = array.shape[-1]
    start = pad_before
    end = depth - pad_after if pad_after > 0 else depth

    # Create slice for all dimensions, with the depth slice at the end
    slices = [slice(None)] * (array.ndim - 1) + [slice(start, end)]

    return array[tuple(slices)]
