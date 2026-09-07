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

"""Spleen Segmentation: Data loading and transform functions (training-only)."""

from logging import INFO
from pathlib import Path

import nibabel as nib
import numpy as np
from flip import FLIP
from flip.constants import ResourceType
from flwr.common import log
from tqdm import tqdm

SEED = 42


class FLIP_BASE:
    def __init__(self):
        self.project_id = ""
        self.query = ""
        self.dataframe = None

        # --- Core FLIP object ---
        self.flip = FLIP()

    def get_image_and_label_list(self, _val_split: float, _test_split: float, is_test: bool = False):
        """
        Returns train, val, and test lists of dicts.

        Each dict contains the path to an image and its corresponding label.

        Args:
            _val_split (float): Fraction of the dataset to use for validation.
            _test_split (float): Fraction of the dataset to use for testing.
            is_test (bool): If True, only return the test set. Otherwise, return train and val sets.

        Returns:
            If is_test is True:
                test_datalist (list): List of dicts containing image and label paths for the test set.
            If is_test is False:
                train_datalist (list): List of dicts containing image and label paths for the training set.
                val_datalist (list): List of dicts containing image and label paths for the validation set.
        """

        datalist = []
        images_found = 0
        # loop over each accession id in the train set
        for accession_id in tqdm(self.dataframe["accession_id"], desc="Preparing dataset", unit="accession"):
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
                log(INFO, "⚠️ Could not fetch images for accession_id=%s: %s", accession_id, err)
                continue

            log(INFO, str(accession_folder_path))

            # get all images in the accession folder that match the pattern "input_*.nii.gz"
            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            images_found += len(all_images)

            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    log(INFO, "⚠️ No matching segmentation for %s", img.name)
                    continue

                try:
                    img_header = nib.load(str(img))
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    log(INFO, "⚠️ Invalid image pair for %s: %s", img.name, err)
                    continue

                # Some QC checks to ensure the image and segmentation are valid and match
                # check is 3D and at least 128x128x128 in size and seg is the same shape as the image
                if len(img_header.shape) != 3:
                    log(INFO, "⚠️ Skipping non-3D image %s", img.name)
                    continue

                if img_header.shape != seg_header.shape:
                    log(
                        INFO,
                        "⚠️ Shape mismatch for %s: image=%s label=%s",
                        img.name,
                        img_header.shape,
                        seg_header.shape,
                    )
                    continue

                datalist.append({"image": str(img), "label": seg})

        log(INFO, "Dataset ready: %d image/label pairs", len(datalist))

        # Fail here rather than letting an empty dataset surface downstream as torch's opaque
        # "num_samples should be a positive integer value, but got num_samples=0".
        if not datalist:
            raise RuntimeError(
                f"No image/label pairs found: {images_found} input_*.nii.gz image(s) across "
                f"{len(self.dataframe['accession_id'])} accession(s), none with a matching label_*.nii.gz. "
                "Supervised training needs a label beside each image in XNAT — was the data-enrichment step "
                "run, and did it run after DICOM-to-NIfTI conversion? See the Data Enrichment user guide."
            )

        # Calculate split indices
        n_total = len(datalist)
        n_test = int(_test_split * n_total)
        n_val = int(_val_split * n_total)
        n_train = n_total - n_val - n_test

        # Shuffle datalist for random split
        rng = np.random.default_rng(seed=SEED)
        rng.shuffle(datalist)

        train_datalist = datalist[:n_train]
        val_datalist = datalist[n_train : n_train + n_val]
        test_datalist = datalist[n_train + n_val :]

        if is_test:
            return test_datalist

        return train_datalist, val_datalist
