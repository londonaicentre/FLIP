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

"""The simulator layout: what a tutorial reads under ``LOCAL_DEV`` in place of an XNAT pull.

``data/brain_mri/images/<accession>/scans/`` holds one 3-D NIfTI per channel, named the way the
platform names a pulled series (``input_<label>_…``), plus the MSD label as ``label_<case>.nii.gz``;
``data/brain_mri/dataframe.csv`` is the cohort (``accession_id`` per study). The accessions are the
same synthetic ones the DICOM path stamps, so a tutorial sees the same ids in the simulator and on
the platform. The channels are split straight out of the 4-D NIfTI rather than round-tripped through
dcm2niix — the tutorials re-orient with ``Orientationd`` anyway.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
from tqdm import tqdm
from utils.synthetic_identity import accession_number

from brain_mri.cases import select_cases
from brain_mri.convert_brain_mri_to_dicom import CHANNELS, DEFAULT_DATASET_JSON, DEFAULT_IMAGES_DIR, read_modalities

DEFAULT_LABELS_DIR = Path("data/Task01_BrainTumour/labelsTr")
DEFAULT_OUT_DIR = Path("data/brain_mri")
DATAFRAME_COLUMNS = ["accession_id", "subject"]


def prepare(
    images_dir: Path, labels_dir: Path, dataset_json: Path, out_dir: Path, cases: list[str]
) -> list[dict[str, str]]:
    """Write the layout for ``cases``; returns the dataframe rows, also written to ``<out_dir>/dataframe.csv``."""
    modalities = read_modalities(dataset_json)
    rows: list[dict[str, str]] = []
    for case in tqdm(cases, desc="cases"):
        accession = accession_number(case)
        scans = Path(out_dir) / "images" / accession / "scans"
        shutil.rmtree(scans, ignore_errors=True)
        scans.mkdir(parents=True)
        image = nib.load(str(Path(images_dir) / f"{case}.nii.gz"))
        volume = np.asanyarray(image.dataobj)
        for index, name in enumerate(modalities):
            nib.save(
                nib.Nifti1Image(volume[..., index], image.affine),
                str(scans / f"input_{CHANNELS[name].label}_{case}.nii.gz"),
            )
        shutil.copyfile(Path(labels_dir) / f"{case}.nii.gz", scans / f"label_{case}.nii.gz")
        rows.append({"accession_id": accession, "subject": case})
    with (Path(out_dir) / "dataframe.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATAFRAME_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅ {len(rows)} case(s) → {out_dir}/images and {out_dir}/dataframe.csv", flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--labels-dir", type=Path, default=DEFAULT_LABELS_DIR)
    parser.add_argument("--dataset-json", type=Path, default=DEFAULT_DATASET_JSON)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--num-cases", type=int, help="the N lowest-numbered cases (default: all)")
    args = parser.parse_args(argv)

    prepare(
        args.images_dir, args.labels_dir, args.dataset_json, args.out_dir, select_cases(args.images_dir, args.num_cases)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
