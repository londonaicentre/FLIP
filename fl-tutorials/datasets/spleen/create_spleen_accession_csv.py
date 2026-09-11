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
from pathlib import Path

import pandas as pd
from natsort import natsorted


def _is_valid_subject_folder(images_dir: str, subject_id: str) -> bool:
    """Check if the given subject folder contains both input and label files."""
    subject_dir = os.path.join(images_dir, subject_id)
    scans_dir = os.path.join(subject_dir, "scans")
    if not os.path.isdir(scans_dir):
        return False

    scan_files = os.listdir(scans_dir)
    has_input = any(name.startswith("input_") and name.endswith(".nii.gz") for name in scan_files)
    has_label = any(name.startswith("label_") and name.endswith(".nii.gz") for name in scan_files)
    return has_input and has_label


def create_accession_csv(images_dir: Path, output_csv: Path) -> int:
    """Create a CSV file containing accession IDs from the reorganized spleen dataset folders."""
    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory does not exist: {images_dir}")
    if not images_dir.is_dir():
        raise NotADirectoryError(f"Images path is not a directory: {images_dir}")

    images_dir_str = str(images_dir)
    subject_ids = []
    for name in os.listdir(images_dir_str):
        if name.startswith("."):
            continue
        if not os.path.isdir(os.path.join(images_dir_str, name)):
            continue
        if _is_valid_subject_folder(images_dir_str, name):
            subject_ids.append(name)

    subject_ids = natsorted(subject_ids)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(subject_ids, columns=["accession_id"])
    df.to_csv(output_csv, index=False)

    return len(subject_ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a CSV with accession IDs from the reorganized spleen dataset folders."
    )
    parser.add_argument(
        "--images_dir",
        "-i",
        type=Path,
        required=True,
        help="Path to the reorganized spleen dataset directory (contains subject_* folders).",
    )
    parser.add_argument(
        "--output_csv",
        "-o",
        type=Path,
        required=True,
        help="Output CSV path.",
    )
    args = parser.parse_args()

    count = create_accession_csv(images_dir=args.images_dir.resolve(), output_csv=args.output_csv.resolve())
    print(f"Wrote {count} accession IDs to {args.output_csv.resolve()}")
