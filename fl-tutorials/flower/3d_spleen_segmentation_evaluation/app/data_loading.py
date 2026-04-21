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
import numpy as np
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

    def get_test_data_list(self, _test_split=None):
        """Returns a list of dicts for test data, each dict containing the path to an image and its corresponding label.
        
        Args:
            _test_split: Optional split ratio. If provided, only returns the test portion of the data.
                        If None, returns all data as test data.
        """

        datalist = []
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

            log(INFO, str(accession_folder_path))

            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            log(INFO, str(all_images))

            this_accession_matches = 0
            log(INFO, f"Total base count found for accession_id {accession_id}: {len(all_images)}")
            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    log(INFO, f"No matching segmentation mask for {img}.")
                    continue

                try:
                    img_header = nib.load(str(img))
                except nib.filebasedimages.ImageFileError as err:
                    log(INFO, f"Problem loading header of base image {str(img)}.")
                    log(INFO, f"{err=}")
                    log(INFO, f"{type(err)=}")
                    log(INFO, f"{err.args=}")
                    continue

                try:
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    log(INFO, f"Problem loading header of segmentation {str(seg)}.")
                    log(INFO, f"{err=}")
                    log(INFO, f"{type(err)=}")
                    log(INFO, f"{err.args=}")
                    continue

                # Some QC checks to ensure the image and segmentation are valid and match
                # check is 3D and at least 128x128x128 in size and seg is the same
                if len(img_header.shape) != 3:
                    log(INFO, f"Image has other than 3 dimensions (it has {len(img_header.shape)}.)")
                    continue
                elif any([img_dim != seg_dim for img_dim, seg_dim in zip(img_header.shape, seg_header.shape)]):
                    log(
                        INFO,
                        f"Image dimensions do not match segmentation dimensions"
                        f"({img_header.shape}) vs ({seg_header.shape}).",
                    )
                    continue
                else:
                    # defines keys for image and segmentation
                    datalist.append({"image": str(img), "label": seg})
                    log(INFO, "Matching base image and segmentation added.")
                    this_accession_matches += 1

            log(INFO, f"Added {this_accession_matches} matched image + segmentation pairs for {accession_id}.")

        log(INFO, f"Found {len(datalist)} files in total.")

        # If test_split is specified, split the data and return only the test portion
        # Otherwise, return all data as test data
        if _test_split is not None and 0 < _test_split < 1:
            split_idx = int((1 - _test_split) * len(datalist))
            _, test_datalist = np.split(datalist, [split_idx])
            return test_datalist
        else:
            return datalist
