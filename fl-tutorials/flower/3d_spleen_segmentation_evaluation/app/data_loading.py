# Copyright (c) 2026 Flower Labs GmbH
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

"""Spleen Segmentation: Data loading and transform functions (evaluation-only)."""

from logging import INFO
from pathlib import Path

import nibabel as nib
from flip import FLIP
from flip.constants import ResourceType
from flwr.common import log


class FLIP_BASE:
    def __init__(self):
        self.project_id = ""
        self.query = ""
        self.dataframe = None

        # --- Core FLIP object ---
        self.flip = FLIP()

    def get_test_data_list(self):
        """Return a list of {"image": path, "label": path} dicts for every matched pair in the cohort.

        This is an evaluation-only tutorial, so every sample in the client's
        cohort is scored — no train/test holdout is applied.

        Args:
            None

        Returns:
            datalist: list[dict[str, str]]: List of dicts with keys "image" and "label" for every matched pair.
        """
        datalist: list[dict[str, str]] = []

        images_found = 0
        # loop over each accession id in the train set
        for accession_id in self.dataframe["accession_id"]:
            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.NIFTI,
                        # ResourceType.SEGMENTATION,
                    ],
                )
            except Exception as err:
                log(INFO, f"Could not get image data folder path for {accession_id}: {err}")
                continue

            # get all images in the accession folder that match the pattern "input_*.nii.gz"
            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            images_found += len(all_images)

            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    log(INFO, f"No matching segmentation mask for {img}.")
                    continue

                try:
                    img_header = nib.load(str(img))
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    log(INFO, f"⚠️ Invalid image pair for {img.name}: {err}")
                    continue

                # Some QC checks to ensure the image and segmentation are valid and match
                # check is 3D and at least 128x128x128 in size and seg is the same shape as the image
                if len(img_header.shape) != 3:
                    log(INFO, f"⚠️ Skipping non-3D image {img.name}")
                    continue

                if img_header.shape != seg_header.shape:
                    log(
                        INFO,
                        f"⚠️ Shape mismatch for {img.name}: image={img_header.shape} label={seg_header.shape}",
                    )
                    continue

                datalist.append({"image": str(img), "label": seg})

        log(INFO, f"Found {len(datalist)} files in total — evaluating all of them.")

        # Fail here rather than letting an empty dataset surface downstream as torch's opaque
        # "num_samples should be a positive integer value, but got num_samples=0".
        if not datalist:
            raise RuntimeError(
                f"No image/label pairs found: {images_found} input_*.nii.gz image(s) across "
                f"{len(self.dataframe['accession_id'])} accession(s), none with a matching label_*.nii.gz. "
                "Supervised evaluation needs a label beside each image in XNAT — was the data-enrichment step "
                "run, and did it run after DICOM-to-NIfTI conversion? See the Data Enrichment user guide."
            )

        return datalist
