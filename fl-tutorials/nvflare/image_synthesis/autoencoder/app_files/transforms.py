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

"""MONAI transforms for the brain-MRI cohort (MSD Task01_BrainTumour).

One 3-D volume per **series**: the study's four co-registered channels (FLAIR, T1w, T1Gd, T2w)
arrive as four separate NIfTI files, and each is an independent single-channel sample here. What
distinguishes them is carried alongside the pixels as ``modality`` / ``modality_index``, which the
latent diffusion tutorial turns into its cross-attention condition — see ``MODALITY_KEYS``.

The chain is short on purpose:

* ``LoadImaged`` + ``EnsureChannelFirstd`` — NIfTI, so no reader is pinned. The X-ray tutorials pin
  ``PydicomReader(swap_ij=False)`` because DICOM ``PixelData`` has no orientation of its own and the
  wrong reader silently transposes it; a NIfTI carries an affine, and ``Orientationd`` below reads it.
* ``Orientationd(axcodes="RAS")`` — the one guarantee that matters. The simulator layout splits the
  channels straight out of the MSD 4-D volume while a platform pull comes through dcm2niix, so the
  two paths can disagree on axis order; reorienting from the affine makes them agree.
* ``Resized`` to :data:`SPATIAL_SHAPE` — a fixed grid, not a fixed voxel size. ``Spacingd`` +
  ``ResizeWithPadOrCropd`` (the spleen chain) preserves millimetres and pads, which suits
  segmentation; a generative model needs every sample on the same grid, and 96 is divisible by
  ``2**2`` so it survives the autoencoder's two downsamplings exactly (96 -> 48 -> 24).
* ``NormalizeIntensityd`` (z-score) — **per volume**, which is the right choice for MR and the wrong one
  for CT. MR intensities have no absolute meaning: the same tissue reads differently across scanners
  and sequences, so a fixed window (the spleen chain's ``ScaleIntensityRanged(a_min=-57, a_max=250)``,
  calibrated Hounsfield units) has nothing to calibrate against here.

By convention a FLIP app declares its transforms here, so the inference chain can be found in one
predictable place when the model is later packaged for deployment. See
``docs/source/working-with-flip-apps/package-model-as-map.rst``.
"""

import monai.transforms as mt

#: Spatial size every volume is resampled to. Must equal ``config.json``'s ``spatial_shape``
#: (pinned by fl-tutorials/tests/test_image_synthesis_config_parity.py).
SPATIAL_SHAPE = [96, 96, 96]

#: Non-image keys a sample carries through the chain. MONAI's dictionary transforms copy unknown
#: keys untouched, and the default collate turns ``modality_index`` into a batch tensor — which is
#: what the latent diffusion tutorial one-hot encodes into its cross-attention condition.
MODALITY_KEYS = ("modality", "modality_index")


def get_brain_mri_transforms(is_validation: bool = False) -> mt.Compose:
    """Return the MONAI transforms used for brain-MRI training/validation.

    Args:
        is_validation (bool): When True, skip random affine augmentation so validation is
            deterministic. This is the chain to transcribe when exporting the model for inference.

    Returns:
        mt.Compose: Composed transform pipeline keyed on "image".
    """
    transforms = [
        mt.LoadImaged(keys=["image"], image_only=True),
        mt.EnsureChannelFirstd(keys=["image"], channel_dim="no_channel"),
        mt.Orientationd(keys=["image"], axcodes="RAS"),
        mt.Resized(keys=["image"], spatial_size=SPATIAL_SHAPE),
        # Z-score rather than min-max, matching the reference MSD brain-tumour recipe. Min-max
        # leaves a skull-stripped volume at a standard deviation of ~0.15, because 82% of it is
        # background pinned at zero — a poorly conditioned input. Normalising gives the brain
        # unit variance. The background becomes a negative constant rather than exactly zero,
        # which is why the foreground mask in `trainer.foreground_ssim` keys off the per-volume
        # minimum instead of a literal zero.
        mt.NormalizeIntensityd(keys=["image"], channel_wise=True),
    ]
    if not is_validation:
        # Small and shape-only: a generative model learns the intensity distribution it is shown, so
        # augmentation here must not touch intensities. padding_mode="border" keeps the rotated
        # corners from introducing a hard zero edge the autoencoder would learn to reproduce.
        transforms.append(
            mt.RandAffined(
                keys=["image"],
                rotate_range=(-0.05, 0.05),
                scale_range=(0.01, 0.05),
                translate_range=(-0.05, 0.05),
                prob=1.0,
                padding_mode="border",
            )
        )
    return mt.Compose(transforms)
