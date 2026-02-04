# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_dir", type=str, help="Directory where the dataset will be extracted")
    return parser.parse_args()


def download_spleen_dataset(filepath, output_dir):
    url = "https://msd-for-monai.s3-us-west-2.amazonaws.com/Task09_Spleen.tar"
    download_and_extract(url=url, filepath=filepath, output_dir=output_dir)


if __name__ == "__main__":
    args = parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    download_spleen_dataset(os.path.join(args.output_dir, "Task09_Spleen.tar"), args.output_dir)

    # Define paths
    base_dir = os.path.join(args.output_dir, "Task09_Spleen")
    images_dir = os.path.join(base_dir, "imagesTr")
    labels_dir = os.path.join(base_dir, "labelsTr")

    # This is where flip looks for the data
    output_dir = args.output_dir

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # List all image files
    image_files = os.listdir(images_dir)

    # Process each image file
    for img_file in image_files:
        if img_file.startswith("."):
            continue

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
            print(f"Copying image and label for {subject_id}")
            shutil.copy(src_img, dest_img)
            shutil.copy(src_label, dest_label)

    print("Reorganization complete.")
