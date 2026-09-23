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

"""Extract per-subject DICOM metadata into the table the OMOP conversion consumes.

Imported from ``londonaicentre/flip_project_spleen_segmentation`` (FLIP#1092). Reads one instance
per subject — the tags of interest are study-level — and writes ``tables/dicom_metadata.csv``.
"""

import glob
import os

import pandas as pd
import pydicom
from natsort import natsorted


def quality_checks(df):
    # Checks before saving the metadata
    # PatientID should be integers
    # assert df['PatientID'].apply(lambda x: isinstance(x, int)).all(), "Not all elements of PatientID are integers"
    # They should also be unique
    assert df["PatientID"].nunique() == len(df), f"Duplicate IDs found! {df[df.duplicated('PatientID', keep=False)]}"
    return True


def extract_dicom_metadata(base_dir):
    """
    Extract metadata from DICOM files in a directory structure.
    Each folder represents a subject, and contains DICOM files.

    Parameters:
    base_dir (str): The base directory containing subject folders.

    Returns:
    pd.DataFrame: A dataframe containing metadata tags.
    """
    data = []

    # Walk through each folder and file
    for subject in natsorted(os.listdir(base_dir)):
        subject_dir = os.path.join(base_dir, subject)
        if not os.path.isdir(subject_dir):
            continue

        # use glob.glob recursive to get .dcm files under the subject folder
        dicom_files = glob.glob(f"{subject_dir}/**/*.dcm", recursive=True)
        if not dicom_files:
            continue

        # Process only the first DICOM file from the subject
        # we're only interested in metadata at the subject level
        file_path = dicom_files[0]

        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True)  # Read DICOM without pixel data
            metadata = {
                "Subject": subject,
                "FileName": os.path.basename(file_path),
                "FilePath": file_path,
            }

            # Extract relevant metadata fields
            for elem in ds:
                if elem.VR != "SQ":  # Skip sequences for simplicity
                    tag_name = elem.name.replace(" ", "").replace("'s", "").replace("s'", "")
                    metadata[tag_name] = elem.value

            # PatientID is actually the AccessionNumber (see convert_spleen_dataset.py)
            # metadata["AccessionNumber"] = metadata["PatientID"]
            # metadata["PatientID"] = subject
            data.append(metadata)

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    # Convert to a DataFrame
    df = pd.DataFrame(data)
    return df


if __name__ == "__main__":
    # Example usage
    base_dir = "./dicom_output"
    metadata_df = extract_dicom_metadata(base_dir)

    # Add ProtocolName and conditioning columns, all with the same value
    # metadata_df["ProtocolName"] = "Abdominal CT"
    metadata_df["conditioning"] = "No conditioning"

    # Perform quality checks
    quality_checks(metadata_df)

    # Display or save the dataframe
    print(metadata_df.head())
    print(len(metadata_df))

    # Save the metadata to a CSV file
    # Create directory if it doesn't exist
    os.makedirs("tables", exist_ok=True)
    metadata_df.to_csv("tables/dicom_metadata.csv", index=False)
