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
from typing import Any

import monai
import monai.transforms as mt
import nibabel as nib
import numpy as np
import pandas as pd
import torch
from monai.data import MetaTensor

IMAGE_KEY = "image"
WHOLE_GLAND_KEY = "whole_gland"
PZ_TZ_KEY = "pz_tz"
# Every volume is reoriented to this before use — see build_loader.
AXCODES = "RAS"


def build_loader() -> mt.Compose:
    """The transform chain that reads one study's image and two masks onto one grid.

    Image and masks are paired by affine, never by array index. On the platform the image an
    fl-client receives is dcm2niix output from XNAT, which stores each volume with the DICOM row
    axis reversed (dcm2niix's ``isFlipY``, on by default and not exposed in the command XNAT
    registers), while the PI-CAI masks uploaded at enrichment are in the ``.mha`` convention
    SimpleITK writes — the same voxels, the other row order. Each file's affine is correct, so
    reading both through ``Orientationd`` lands them on one grid; reading raw arrays mirrors the
    mask across the image's anterior-posterior midline (Dice 0.52 against its true position on
    10000_1000000). The simulator's own data goes through the same chain, so the two paths produce
    identical tensors — ``tests/test_prostate_dataset_orientation.py`` pins both.

    The zonal (HeviAI23) and whole-gland (Bosma22b) labels are independent AI submissions and do
    not always share a grid, so both masks are resampled onto the *image's* grid by affine
    (nearest, zero-padded — a label must not bleed past its own extent). When a mask already sits
    on that grid the resample is the identity.

    Returns:
        mt.Compose: ``{IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY}`` paths in; channel-first ``AXCODES``
        MetaTensors on the image grid out.
    """
    keys = [IMAGE_KEY, WHOLE_GLAND_KEY, PZ_TZ_KEY]
    return mt.Compose(
        [
            mt.LoadImaged(keys=keys, ensure_channel_first=True, image_only=True),
            # labels=None is MONAI's announced default (axis labels from the tensor's own space); passing
            # it now opts in rather than tripping the FutureWarning the test suite escalates.
            mt.Orientationd(keys=keys, axcodes=AXCODES, labels=None),
            mt.ResampleToMatchd(
                keys=[WHOLE_GLAND_KEY, PZ_TZ_KEY], key_dst=IMAGE_KEY, mode="nearest", padding_mode="zeros"
            ),
        ]
    )


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
        self.loader = build_loader()
        self.df = pd.read_csv(self.site_dir / "manifest.csv", dtype=str)

    def __len__(self) -> int:
        return len(self.df)

    @staticmethod
    def combine_masks(whole_gland: torch.Tensor, pz_tz: torch.Tensor) -> torch.Tensor:
        """Combine the whole-prostate and PZ/TZ masks into one 3-channel mask.

        Both arrive from the loader already on the image grid (see build_loader), so this is
        purely a relabelling.

        Args:
            whole_gland: (1, H, W, D) whole-gland mask, 1 inside the gland.
            pz_tz: (1, H, W, D) zonal mask (1=PZ, 2=TZ).

        Returns:
            torch.Tensor: (3, H, W, D) mask, channels [whole_gland, pz, tz].
        """
        whole_gland_labels = torch.round(whole_gland[0]).to(torch.int8)
        pz_tz_labels = torch.round(pz_tz[0]).to(torch.int8)
        mask = torch.zeros((3, *whole_gland_labels.shape), dtype=torch.float32)
        mask[0][whole_gland_labels == 1] = 1
        mask[1][pz_tz_labels == 1] = 1  # pz
        mask[2][pz_tz_labels == 2] = 1  # tz
        return mask

    @staticmethod
    def as_fingerprint_pair(image: MetaTensor, mask: torch.Tensor) -> dict[str, nib.Nifti1Image]:
        """Wrap a loaded scan and its combined mask as the NIfTI pair the fingerprint wants.

        Both arrays carry a leading channel axis, so the header gets a matching
        dummy zoom in front of the `(h, w, d)` spacing — the `(1.0, h, w, d)`
        layout upstream's `PicSegDataset.load()` writes, and the one
        `DatasetFingerprintExtractor.analyze_case` expects: it drops that first
        zoom and reorders the rest to `(d, h, w)` itself, to match the
        `c h w d -> c d h w` rearrange it applies to the arrays.

        Args:
            image: (1, H, W, D) scan as the loader returns it; its affine is the pair's affine.
            mask: (3, H, W, D) combined mask, channels [whole_gland, pz, tz].

        Returns:
            dict: `{"image": (1, H, W, D) NIfTI, "mask": (3, H, W, D) NIfTI}`.
        """
        affine = np.asarray(image.affine, dtype=np.float64)
        zooms = (1.0, *(float(z) for z in nib.affines.voxel_sizes(affine)))
        pair = {}
        for name, array in (("image", image.cpu().numpy()), ("mask", mask.cpu().numpy())):
            nii = nib.Nifti1Image(array, affine)
            nii.header.set_zooms(zooms)
            pair[name] = nii
        return pair

    def __getitem__(self, idx: int) -> dict[str, Any]:
        row = self.df.iloc[idx]
        patient_id, study_id = row["patient_id"], row["study_id"]
        accession_id = f"{patient_id}_{study_id}"

        loaded = self.loader(
            {
                IMAGE_KEY: self.nifti_dir / f"{accession_id}_{self.modality}.nii.gz",
                WHOLE_GLAND_KEY: self.wp_mask_dir / f"{accession_id}.nii.gz",
                PZ_TZ_KEY: self.pz_tz_mask_dir / f"{accession_id}.nii.gz",
            }
        )
        image = loaded[IMAGE_KEY]
        mask = self.combine_masks(loaded[WHOLE_GLAND_KEY], loaded[PZ_TZ_KEY])

        if self.fingerprint:
            return self.as_fingerprint_pair(image, mask)

        data = {
            "image": image.as_tensor().to(torch.float32),
            "mask": mask.to(torch.float16),
            "accession_id": accession_id,
        }

        if self.transform is not None:
            data = self.transform(data)

        return data
