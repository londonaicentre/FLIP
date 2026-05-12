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

"""Chest-X-ray data loading and label extraction for FLIP."""

from logging import INFO
from typing import List, Sequence

import numpy as np
import pydicom
import torch
from flip import FLIP
from flip.constants import ResourceType
from flwr.common import log
from pandas import Series
from pydantic import BaseModel


class Lesion(BaseModel):
    id: int
    lesion: str


class LesionDict(BaseModel):
    items: Sequence[Lesion]

    def contains(self, element_value: str) -> bool:
        """Return True if any lesion has this name."""
        return any(item.lesion == element_value for item in self.items)

    def get_lesion_list(self) -> List[str]:
        """Return all lesion names in declaration order."""
        return [item.lesion for item in self.items]


def get_labels_from_radiology_row(
    radiology_row: Series,
    lesions: LesionDict,
    value_to_numerical: dict,
    normal_label: str = "Lungs in normal arrangement",
) -> dict:
    """Convert a dataframe row into a {lesion_name: 0/1/-1} label dict.

    Args:
        radiology_row (Series): One row from the FLIP cohort dataframe.
        lesions (LesionDict): The lesions to score.
        value_to_numerical (dict): Maps {0: "No", 1: "Yes"} (or whatever string
            values appear in the dataframe) so we can recover the binary label.
        normal_label (str): When this column is "Yes", every lesion is forced
            to negative regardless of its own column.

    Returns:
        dict: ``{lesion_name: 0|1|-1}``. -1 marks unknown / not annotated;
        the BCE loss masks these out.
    """
    out_dict = {}
    for lesion in lesions.items:
        lesion_name = lesion.lesion
        if normal_label in radiology_row.keys() and radiology_row[normal_label] == value_to_numerical[1]:
            out_dict[lesion_name] = 0
        elif lesion_name in radiology_row.keys():
            if radiology_row[lesion_name] == value_to_numerical[1]:
                out_dict[lesion_name] = 1
            elif radiology_row[lesion_name] == value_to_numerical[0]:
                out_dict[lesion_name] = 0
            else:
                out_dict[lesion_name] = -1
    return out_dict


def get_lesion_label(in_batch: dict, lesions: LesionDict) -> torch.Tensor:
    """Stack per-lesion label scalars into a [batch_size, n_lesions] tensor."""
    out_tensor = [in_batch[les.lesion] for les in sorted(lesions.items, key=lambda x: x.id)]
    return torch.stack(out_tensor, dim=1).float()


class FLIP_BASE:
    """Wraps FLIP cohort + image fetching for chest-X-ray training.

    Mirrors the spleen tutorial's FLIP_BASE shape so the base server_app and
    the per-tutorial client_app stay symmetric across tutorials.
    """

    def __init__(self) -> None:
        self.project_id: str = ""
        self.query: str = ""
        self.dataframe = None
        self.flip = FLIP()

    def get_image_and_label_list(
        self,
        lesions: LesionDict,
        value_to_numerical: dict,
        normal_key: str,
        val_split: float,
        test_split: float,
        is_test: bool = False,
    ):
        """Return train/val (or test) datalists of {"image": dcm_path, **labels}.

        Walks the FLIP-supplied cohort dataframe, pulls each accession's DICOM
        files, and pairs every readable .dcm with the row's lesion labels.

        Args:
            lesions (LesionDict): Active lesions to score.
            value_to_numerical (dict): {0: "No", 1: "Yes"} mapping for the
                dataframe's string-valued columns.
            normal_key (str): Column name that, when "Yes", forces all lesions
                negative for that row.
            val_split (float): Validation fraction (0–1).
            test_split (float): Test fraction (0–1).
            is_test (bool): Return only the test split when True; otherwise
                return ``(train_datalist, val_datalist)``.

        Returns:
            list | tuple[list, list]: Either the test datalist or a
            ``(train, val)`` pair, depending on ``is_test``.
        """
        if self.dataframe is None:
            raise RuntimeError("FLIP_BASE.dataframe not populated; call flip.get_dataframe first.")

        datalist = []
        for _, row in self.dataframe.iterrows():
            accession_id = row["accession_id"]
            pathology_dict = get_labels_from_radiology_row(row, lesions, value_to_numerical, normal_key)

            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[ResourceType.DICOM],
                )
            except Exception as err:
                log(INFO, f"Could not get image data folder path for {accession_id}: {err}")
                continue

            all_images = list(accession_folder_path.rglob("*.dcm"))
            this_accession_matches = 0
            log(INFO, f"Total base count found for accession_id {accession_id}: {len(all_images)}")

            for img in all_images:
                try:
                    _ = pydicom.dcmread(str(img))
                except Exception as e:
                    log(INFO, f"Problem loading header of base image {str(img)}: {e}")
                    continue

                item_ = {"image": str(img)}
                item_.update(pathology_dict)
                datalist.append(item_)
                this_accession_matches += 1

            log(INFO, f"Added {this_accession_matches} image / label pairs for {accession_id}.")

        log(INFO, f"Found {len(datalist)} files in total.")

        train_datalist, val_datalist, test_datalist = np.split(
            datalist,
            [
                int(len(datalist) * (1 - val_split - test_split)),
                int(len(datalist) * (1 - test_split)),
            ],
        )

        log(
            INFO,
            f"Split: {len(train_datalist)} train, {len(val_datalist)} val, {len(test_datalist)} test.",
        )

        if is_test:
            return list(test_datalist)
        return list(train_datalist), list(val_datalist)
