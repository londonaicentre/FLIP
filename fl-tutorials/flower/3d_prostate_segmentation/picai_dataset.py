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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/dataset/picai_dataset.py,
# trimmed to what this tutorial's data actually has: the PI-CAI train/valid
# case (no PZ/TZ inference-only ProstateX branch, no fingerprint mode), reading
# from a per-site folder produced by partition_by_center.py. The 3-zone mask
# (whole gland / PZ / TZ) is combined the same way as upstream.

from collections.abc import Callable
from pathlib import Path

import monai
import nibabel as nib
import numpy as np
import pandas as pd
import torch
from einops import rearrange
from nibabel.processing import resample_from_to


class PicaiSegDataset(monai.data.Dataset):
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
    ) -> None:
        """Args:
        site_dir: Path to a `data/sites/<CENTER>` folder (holds `manifest.csv`,
            `nifti/`, `labels/`, `zonal_labels/`).
        modality: Which PI-CAI scan modality to load as the image (`t2w`, `adc`, or `hbv`).
        transform: Optional MONAI transform applied to the `{"image", "mask"}` dict.
        """
        self.site_dir = Path(site_dir)
        self.nifti_dir = self.site_dir / "nifti"
        self.wp_mask_dir = self.site_dir / "labels"
        self.pz_tz_mask_dir = self.site_dir / "zonal_labels"
        self.modality = modality
        self.transform = transform
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

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        row = self.df.iloc[idx]
        patient_id, study_id = row["patient_id"], row["study_id"]

        img_path = self.nifti_dir / f"{patient_id}_{study_id}_{self.modality}.nii.gz"
        wp_mask_path = self.wp_mask_dir / f"{patient_id}_{study_id}.nii.gz"
        pz_tz_mask_path = self.pz_tz_mask_dir / f"{patient_id}_{study_id}.nii.gz"

        image = np.expand_dims(nib.load(img_path).get_fdata(), axis=0)
        mask = rearrange(self.combine_masks(wp_mask_path, pz_tz_mask_path), "h w d c -> c h w d")

        data = {
            "image": torch.as_tensor(image, dtype=torch.float32),
            "mask": torch.as_tensor(mask, dtype=torch.float16),
            "study_id": f"{patient_id}_{study_id}",
        }

        if self.transform is not None:
            data = self.transform(data)

        return data
