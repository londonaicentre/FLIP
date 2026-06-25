# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.
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

import argparse
import os
import shutil

from monai.apps.utils import download_and_extract

MAX_CASES = 41


def download_spleen_dataset(filepath, output_dir):
    """
    Downloads the spleen dataset from the specified URL and extracts it to the given output directory.

    Args:
        filepath (str): The file path where the downloaded compressed file will be saved.
        output_dir (str): The target directory where the extracted files will be saved.
    """
    url = "https://msd-for-monai.s3-us-west-2.amazonaws.com/Task09_Spleen.tar"
    download_and_extract(url=url, filepath=filepath, output_dir=output_dir)


def reorganise_spleen_dataset(output_dir, num_cases):
    """
    Reorganizes the downloaded spleen dataset into a structure where each subject has its own folder containing both the
    image and label files.

    The original dataset has separate folders for images and labels, e.g.

        output_dir/
        └── Task09_Spleen/
            ├── imagesTr/
            │   ├── spleen_1.nii.gz
            │   ├── spleen_2.nii.gz
            │   └── ...
            └── labelsTr/
                ├── spleen_1.nii.gz
                ├── spleen_2.nii.gz
                └── ...

    The expected output directory structure after reorganization will be:

        output_dir/
        ├── subject_1/
        │   └── scans/
        │       ├── input_spleen_1.nii.gz
        │       └── label_spleen_1.nii.gz
        ├── subject_2/
        │   └── scans/
        │       ├── input_spleen_2.nii.gz
        │       └── label_spleen_2.nii.gz
        └── ...

    Args:
        output_dir (str): The directory where the original downloaded dataset is located and where the reorganized
        dataset will be saved.
        num_cases (int): Number of cases to keep from the dataset.
    """
    base_dir = os.path.join(output_dir, "Task09_Spleen")
    images_dir = os.path.join(base_dir, "imagesTr")
    labels_dir = os.path.join(base_dir, "labelsTr")

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # List all image files
    image_files = sorted(os.listdir(images_dir))

    # Process each image file
    print(f"Copying up to {num_cases} images and labels to subject folders in {output_dir}...")

    copied_cases = 0
    for img_file in image_files:
        if img_file.startswith("."):
            continue
        if copied_cases >= num_cases:
            break

        # Extract subject ID (Assuming filename format: subjectID.extension)
        subject_id = img_file.replace(".nii.gz", "").replace("spleen", "subject")

        # Corresponding label file (assuming same filename)
        label_file = img_file

        # Define subject folder path
        subject_folder = os.path.join(output_dir, subject_id)
        os.makedirs(subject_folder, exist_ok=True)

        # Move files to subject folder
        dest_folder = os.path.join(subject_folder, "scans")

        src_img = os.path.join(images_dir, img_file)
        src_label = os.path.join(labels_dir, label_file)
        dest_img = os.path.join(dest_folder, img_file.replace("spleen", "input_spleen"))
        dest_label = os.path.join(dest_folder, label_file.replace("spleen", "label_spleen"))

        if os.path.exists(src_img) and os.path.exists(src_label):
            os.makedirs(dest_folder, exist_ok=True)
            shutil.copy(src_img, dest_img)
            shutil.copy(src_label, dest_label)
            copied_cases += 1

    print(f"Reorganization complete. Kept {copied_cases} cases.")

    # Delete the original downloaded dataset to save space, as well as the compressed file
    print("Cleaning up original downloaded dataset ...")
    shutil.rmtree(base_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--filepath",
        "-f",
        type=str,
        help="the file path of the downloaded compressed file.",
        default="./data/Task09_Spleen.tar",
    )
    parser.add_argument(
        "--output_dir", "-o", type=str, help="target directory to save extracted files.", default="./data"
    )
    parser.add_argument(
        "--num_cases",
        "-n",
        type=int,
        default=10,
        help=f"number of cases to keep after download and reorganization (1 to {MAX_CASES}).",
    )
    args = parser.parse_args()

    if args.num_cases < 1 or args.num_cases > MAX_CASES:
        raise ValueError(f"--num_cases must be between 1 and {MAX_CASES}. Received: {args.num_cases}")

    output_dir = os.path.abspath(args.output_dir)

    if os.path.exists(output_dir):
        # Error to prevent overwriting existing data
        raise FileExistsError(
            f"The output directory {output_dir} already exists.Please choose a different directory or remove it."
        )
    else:
        os.makedirs(output_dir)

    print(f"Downloading data to {output_dir}")

    tar_filepath = os.path.join(output_dir, "Task09_Spleen.tar")
    hidden_tar_filepath = os.path.join(output_dir, "._Task09_Spleen")

    download_spleen_dataset(tar_filepath, output_dir)

    reorganise_spleen_dataset(output_dir, args.num_cases)

    # Delete the compressed files after extraction and reorganization to save space
    print("Cleaning up compressed files ...")
    if os.path.exists(tar_filepath):
        os.remove(tar_filepath)
    if os.path.exists(hidden_tar_filepath):
        os.remove(hidden_tar_filepath)
