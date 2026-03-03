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

import logging
from pathlib import Path

import nibabel as nib
import numpy as np
from flip import FLIP
from flip.constants import ResourceType


class FLIP_BASE:
    def __init__(self):
        self.project_id = ""
        self.query = ""
        self.dataframe = None

        # --- Core FLIP object ---
        self.flip = FLIP()

        # --- Logging setup ---
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

    def get_image_and_label_list(self, _val_split, _test_split, is_test: bool=False):
        """Returns train, val, and test lists of dicts, each dict containing the path to an image and its corresponding label."""

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
                print(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            print(accession_folder_path)

            all_images = list(accession_folder_path.rglob("input_*.nii.gz"))
            print(all_images)

            this_accession_matches = 0
            print(f"Total base count found for accession_id {accession_id}: {len(all_images)}")
            for img in all_images:
                # for each image, find the corresponding segmentation mask
                seg = str(img).replace("/input_", "/label_")

                if not Path(seg).exists():
                    print(f"No matching segmentation mask for {img}.")
                    continue

                try:
                    img_header = nib.load(str(img))
                except nib.filebasedimages.ImageFileError as err:
                    print(f"Problem loading header of base image {str(img)}.")
                    print(f"{err=}")
                    print(f"{type(err)=}")
                    print(f"{err.args=}")
                    continue

                try:
                    seg_header = nib.load(seg)
                except nib.filebasedimages.ImageFileError as err:
                    print(f"Problem loading header of segmentation {str(seg)}.")
                    print(f"{err=}")
                    print(f"{type(err)=}")
                    print(f"{err.args=}")
                    continue

                # Some QC checks to ensure the image and segmentation are valid and match
                # check is 3D and at least 128x128x128 in size and seg is the same
                if len(img_header.shape) != 3:
                    print(f"Image has other than 3 dimensions (it has {len(img_header.shape)}.)")
                    continue
                elif any([img_dim != seg_dim for img_dim, seg_dim in zip(img_header.shape, seg_header.shape)]):
                    print(
                        f"Image dimensions do not match segmentation dimensions"
                        f"({img_header.shape}) vs ({seg_header.shape})."
                    )
                    continue
                else:
                    # defines keys for image and segmentation
                    datalist.append({"image": str(img), "label": seg})
                    print("Matching base image and segmentation added.")
                    this_accession_matches += 1

            print(f"Added {this_accession_matches} matched image + segmentation pairs for {accession_id}.")

        print(f"Found {len(datalist)} files in total.")

        # Calculate split indices
        n_total = len(datalist)
        n_test = int(_test_split * n_total)
        n_val = int(_val_split * n_total)
        n_train = n_total - n_val - n_test

        # Shuffle datalist for random split
        np.random.shuffle(datalist)

        train_datalist = datalist[:n_train]
        val_datalist = datalist[n_train:n_train + n_val]
        test_datalist = datalist[n_train + n_val:]

        if is_test:
            return test_datalist
        else:
            return train_datalist, val_datalist
