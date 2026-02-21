# Copyright (c) 2026 Flower Labs GmbH
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

import pandas as pd
import os
from pathlib import Path
from enum import Enum
import pydicom
import nibabel as nib


class ResourceType(str, Enum):
    DICOM = "DICOM"
    NIFTI = "NIFTI"
    SEGMENTATION = "SEG"
    ALL = "ALL"


resource_type_dict = {
    ResourceType.NIFTI: [".nii", ".nii.gz"],
    ResourceType.SEGMENTATION: [".nii", ".nii.gz"],
}


def get_data():
    dataframe = os.environ.get("DEV_DATAFRAME", None)
    if not dataframe:
        raise ValueError(
            "No dataframe path provided. Please set the DATAFRAME environment variable or provide an input_dataframe argument."
        )
    if not os.path.exists(dataframe):
        raise FileNotFoundError(
            f"Dataframe file not found at {dataframe}. Please check the path and try again."
        )
    try:
        df = pd.read_csv(dataframe)
        if "accession_id" not in df.columns:
            raise ValueError(
                "Dataframe must contain an 'accession_id' column. Please check the dataframe format."
            )
        return df
    except Exception as e:
        raise ValueError(f"Error loading dataframe from {dataframe}: {e}")


def get_images_from_accession_id(accession_id: str, attempt_loading: bool = True):
    data_dir = os.environ.get("DEV_IMAGES_DIR", None)
    if not data_dir:
        raise ValueError(
            "No data directory provided. Please set the DEV_IMAGES_DIR environment variable."
        )
    try:
        accession_folder_path = Path(data_dir) / accession_id
        # Makes sure that resource type matches
        input_image = []
        input_segmentation = []
        for ext in resource_type_dict[ResourceType.NIFTI]:
            input_image.extend(accession_folder_path.rglob(f"input_*{ext}"))
        # We look for corresponding label
        for ext in resource_type_dict[ResourceType.SEGMENTATION]:
            input_segmentation.extend(accession_folder_path.rglob(f"label_*{ext}"))
        if len(input_image) > 1 or len(input_segmentation) > 1:
            print(
                f"Ambiguous data found for {accession_id}: more than one image and segmentation results."
            )

        elif len(input_image) == 0 or len(input_segmentation) == 0:
            print(f"No data found for {accession_id}.")
            return None, None

        input_image_path = input_image[0]
        input_segmentation_path = input_segmentation[0]
        if attempt_loading:
            # Attempt to load the data to verify it can be read correctly
            try:
                if ResourceType.NIFTI in resource_type_dict:
                    nib.load(input_image_path)
                    nib.load(input_segmentation_path)
                elif ResourceType.DICOM in resource_type_dict:
                    pydicom.dcmread(input_image_path)
                    pydicom.dcmread(input_segmentation_path)
                print(f"Successfully loaded data for {accession_id}.")
            except Exception as e:
                print(f"Error loading data for {accession_id}: {e}")
                return None, None
        return str(input_image_path), str(input_segmentation_path)

    except Exception as err:
        print(f"Could not get image data folder path for {accession_id}: {err}")
        return None, None
