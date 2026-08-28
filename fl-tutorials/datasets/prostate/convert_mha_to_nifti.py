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
# Converts PI-CAI .mha scans to .nii.gz, preserving the
# <patient_id>/<patient_id>_<study_id>_<modality>.mha naming as
# <patient_id>/<patient_id>_<study_id>_<modality>.nii.gz.

import argparse
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import SimpleITK as sitk
from tqdm import tqdm


def _convert_one(mha_path: Path, input_dir: Path, output_dir: Path) -> None:
    nii_path = (output_dir / mha_path.relative_to(input_dir)).with_suffix(".nii.gz")
    nii_path.parent.mkdir(parents=True, exist_ok=True)
    image = sitk.ReadImage(str(mha_path))
    sitk.WriteImage(image, str(nii_path))


def convert_archive(input_dir: Path, output_dir: Path, workers: int) -> None:
    mha_paths = sorted(input_dir.rglob("*.mha"))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_convert_one, mha_path, input_dir, output_dir) for mha_path in mha_paths]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Converting to NIfTI", unit="scan"):
            future.result()


if __name__ == "__main__":
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    parser = argparse.ArgumentParser(description="Convert PI-CAI .mha scans to .nii.gz")
    parser.add_argument("--input", type=Path, default=default_data_dir / "images")
    parser.add_argument("--output", type=Path, default=default_data_dir / "nifti")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()
    convert_archive(args.input, args.output, args.workers)
