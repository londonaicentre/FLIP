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
import csv
import re
from pathlib import Path


def _subject_sort_key(subject_id: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", subject_id)
    if match:
        return (int(match.group(1)), subject_id)
    return (10**9, subject_id)


def _is_valid_subject_folder(subject_dir: Path) -> bool:
    scans_dir = subject_dir / "scans"
    if not scans_dir.is_dir():
        return False

    input_files = list(scans_dir.glob("input_*.nii.gz"))
    label_files = list(scans_dir.glob("label_*.nii.gz"))
    return len(input_files) > 0 and len(label_files) > 0


def create_accession_csv(images_dir: Path, output_csv: Path) -> int:
    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory does not exist: {images_dir}")
    if not images_dir.is_dir():
        raise NotADirectoryError(f"Images path is not a directory: {images_dir}")

    subject_ids = []
    for child in images_dir.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if _is_valid_subject_folder(child):
            subject_ids.append(child.name)

    subject_ids = sorted(subject_ids, key=_subject_sort_key)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["accession_id"])
        for subject_id in subject_ids:
            writer.writerow([subject_id])

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
