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
# Converts PI-CAI .mha scans to DICOM series with correct per-slice headers
# (PatientID, StudyInstanceUID, SeriesInstanceUID, ImagePositionPatient,
# ImageOrientationPatient, PixelSpacing, SliceThickness) so they can be pulled
# into a trust's PACS (Orthanc) like any other DICOM study.
#
# Adapted from picai_prep (https://github.com/DIAGNijmegen/picai_prep), which
# converts DICOM -> MHA via SimpleITK and reads its archive as
# <patient_id>/<patient_id>_<study_id>_<modality>.mha. This script runs that
# conversion in reverse.

import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import SimpleITK as sitk
from tqdm import tqdm

MODALITY_DESCRIPTIONS = {"t2w": "T2 Weighted", "adc": "ADC Map", "hbv": "High B-Value DWI"}


def write_dicom_series(image: sitk.Image, out_dir: Path, patient_id: str, study_id: str, modality: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    image = sitk.Cast(image, sitk.sitkInt16)

    modification_date = time.strftime("%Y%m%d")
    modification_time = time.strftime("%H%M%S")
    study_uid = f"1.2.826.0.1.3680043.2.1125.{modification_date}.{abs(hash((patient_id, study_id))) % 10**10}"
    series_uid = f"{study_uid}.{abs(hash(modality)) % 1000}"
    frame_of_reference_uid = f"{series_uid}.1"

    direction = image.GetDirection()
    orientation = "\\".join(
        str(v) for v in (direction[0], direction[3], direction[6], direction[1], direction[4], direction[7])
    )
    spacing = image.GetSpacing()

    series_tag_values = {
        "0008|0050": f"{patient_id}_{study_id}",
        "0008|0060": "MR",
        "0008|0020": modification_date,
        "0008|0030": modification_time,
        "0008|103e": MODALITY_DESCRIPTIONS.get(modality, modality.upper()),
        "0010|0020": patient_id,
        "0020|000d": study_uid,
        "0020|0010": study_id,
        "0020|000e": series_uid,
        "0020|0052": frame_of_reference_uid,
        "0020|0037": orientation,
        "0028|0030": f"{spacing[1]}\\{spacing[0]}",
        "0018|0050": str(spacing[2]),
    }

    writer = sitk.ImageFileWriter()
    writer.KeepOriginalImageUIDOn()

    for i in range(image.GetDepth()):
        image_slice = image[:, :, i]
        for tag, value in series_tag_values.items():
            image_slice.SetMetaData(tag, value)
        image_slice.SetMetaData("0008|0012", modification_date)
        image_slice.SetMetaData("0008|0013", modification_time)
        image_slice.SetMetaData("0020|0013", str(i))
        image_slice.SetMetaData(
            "0020|0032", "\\".join(str(v) for v in image.TransformIndexToPhysicalPoint((0, 0, i)))
        )
        writer.SetFileName(str(out_dir / f"{i:04d}.dcm"))
        writer.Execute(image_slice)


def _convert_one(mha_path: Path, output_dir: Path) -> None:
    patient_id, study_id, modality = mha_path.stem.rsplit("_", 2)
    series_dir = output_dir / patient_id / study_id / modality
    image = sitk.ReadImage(str(mha_path))
    write_dicom_series(image, series_dir, patient_id, study_id, modality)


def convert_archive(input_dir: Path, output_dir: Path, workers: int) -> None:
    mha_paths = sorted(input_dir.rglob("*.mha"))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_convert_one, mha_path, output_dir) for mha_path in mha_paths]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Converting to DICOM", unit="scan"):
            future.result()


if __name__ == "__main__":
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    parser = argparse.ArgumentParser(description="Convert PI-CAI .mha scans to DICOM series")
    parser.add_argument("--input", type=Path, default=default_data_dir / "images")
    parser.add_argument("--output", type=Path, default=default_data_dir / "dicom")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()
    convert_archive(args.input, args.output, args.workers)
