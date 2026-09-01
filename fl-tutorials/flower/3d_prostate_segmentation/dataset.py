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
# Adapted from PicSegDataset in
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/dataset/picai_dataset.py

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import monai
import nibabel as nib
import numpy as np
import pandas as pd
import torch
from einops import rearrange
from nibabel.processing import resample_from_to


class PicaiDataset(monai.data.Dataset):
    """3-zone (whole gland, PZ, TZ) prostate segmentation dataset for one PI-CAI site.

    Reads `manifest.csv` (patient_id, study_id) from a site folder produced by
    `partition_by_center.py`, and loads the matching `<modality>` scan from
    that site's `nifti/` folder, whole-gland mask from `labels/`, and PZ/TZ
    mask from `zonal_labels/`.
    """

    def __init__(
        self,
        site_dir: Path,
        modality: str = "t2w",
        transform: Callable | None = None,
        fingerprint: bool = False,
    ) -> None:
        """Args:
        site_dir: Path to a `data/sites/<CENTER>` folder (holds `manifest.csv`,
            `nifti/`, `labels/`, `zonal_labels/`).
        modality: Which PI-CAI scan modality to load as the image (`t2w`, `adc`, or `hbv`).
        transform: Optional MONAI transform applied to the `{"image", "mask"}` dict.
        fingerprint: Yield NIfTI images instead of tensors, for
            `calculate_dataset_fingerprint_segmentation.py`. `transform` is
            ignored in this mode — the fingerprint has to describe the raw data.
        """
        self.site_dir = Path(site_dir)
        self.nifti_dir = self.site_dir / "nifti"
        self.wp_mask_dir = self.site_dir / "labels"
        self.pz_tz_mask_dir = self.site_dir / "zonal_labels"
        self.modality = modality
        self.transform = transform
        self.fingerprint = fingerprint
        self.df = pd.read_csv(self.site_dir / "manifest.csv", dtype=str)

    def __len__(self) -> int:
        return len(self.df)

    def combine_masks(self, wp_mask_path: Path, pz_tz_mask_path: Path) -> np.ndarray:
        """Combine the whole-prostate and PZ/TZ masks into one 3-channel mask.

        The whole-gland (Bosma22b) and PZ/TZ (HeviAI23) labels are independent
        AI submissions and don't always share a grid for the same study; the
        PZ/TZ mask is resampled (nearest-neighbor) onto the whole-gland grid
        when their shapes disagree.

        Args:
            wp_mask_path: Path to the whole-gland mask.
            pz_tz_mask_path: Path to the PZ/TZ mask (1=PZ, 2=TZ).

        Returns:
            np.ndarray: (H, W, D, 3) mask, channels [whole_gland, pz, tz].
        """
        wp_nii = nib.load(wp_mask_path)
        pz_tz_nii = nib.load(pz_tz_mask_path)
        if pz_tz_nii.shape != wp_nii.shape:
            pz_tz_nii = resample_from_to(pz_tz_nii, wp_nii, order=0)

        wp_data = np.round(wp_nii.get_fdata()).astype(np.int8)
        pz_tz_data = np.round(pz_tz_nii.get_fdata()).astype(np.int8)

        mask = np.zeros(wp_data.shape + (3,))
        mask[wp_data == 1, 0] = 1
        mask[pz_tz_data == 1, 1] = 1  # pz
        mask[pz_tz_data == 2, 2] = 1  # tz
        return mask

    def as_fingerprint_pair(
        self, img_nii: nib.Nifti1Image, mask: np.ndarray
    ) -> dict[str, nib.Nifti1Image]:
        """Wrap a loaded scan and its combined mask as the NIfTI pair the fingerprint wants.

        Both arrays carry a leading channel axis, so the header gets a matching
        dummy zoom in front of the `(h, w, d)` spacing — the `(1.0, h, w, d)`
        layout upstream's `PicSegDataset.load()` writes, and the one
        `DatasetFingerprintExtractor.analyze_case` expects: it drops that first
        zoom and reorders the rest to `(d, h, w)` itself, to match the
        `c h w d -> c d h w` rearrange it applies to the arrays.

        Args:
            img_nii: The scan as loaded from `nifti/`.
            mask: (3, H, W, D) combined mask, channels [whole_gland, pz, tz].

        Returns:
            dict: `{"image": (1, H, W, D) NIfTI, "mask": (3, H, W, D) NIfTI}`.
        """
        zooms = (1.0,) + tuple(img_nii.header.get_zooms()[:3])

        image = np.expand_dims(img_nii.get_fdata(), axis=0)
        pair = {}
        for name, array in (("image", image), ("mask", mask)):
            nii = img_nii.__class__(array, img_nii.affine, img_nii.header)
            nii.header.set_data_shape(array.shape)
            nii.header.set_zooms(zooms)
            pair[name] = nii
        return pair

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.df.iloc[idx]
        patient_id, study_id = row["patient_id"], row["study_id"]
        accession_id = f"{patient_id}_{study_id}"

        img_path = self.nifti_dir / f"{accession_id}_{self.modality}.nii.gz"
        wp_mask_path = self.wp_mask_dir / f"{accession_id}.nii.gz"
        pz_tz_mask_path = self.pz_tz_mask_dir / f"{accession_id}.nii.gz"

        img_nii = cast(nib.Nifti1Image, nib.load(img_path))
        mask = rearrange(
            self.combine_masks(wp_mask_path, pz_tz_mask_path), "h w d c -> c h w d"
        )

        if self.fingerprint:
            return self.as_fingerprint_pair(img_nii, mask)

        image = np.expand_dims(img_nii.get_fdata(), axis=0)

        data = {
            "image": torch.as_tensor(image, dtype=torch.float32),
            "mask": torch.as_tensor(mask, dtype=torch.float16),
            "accession_id": accession_id,
        }

        if self.transform is not None:
            data = self.transform(data)

        return data
