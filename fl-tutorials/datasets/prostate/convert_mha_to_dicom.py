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
import csv
import hashlib
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import SimpleITK as sitk
from tqdm import tqdm

MODALITY_DESCRIPTIONS = {"t2w": "T2 Weighted", "adc": "ADC Map", "hbv": "High B-Value DWI"}

# Study/series/frame-of-reference UIDs are derived from this root plus stable identifiers
# (patient_id, study_id, modality) via SHA-256 — never from the machine clock or Python's
# per-process-salted hash(). That makes re-converting the same study, on any machine, on any day,
# produce byte-identical UIDs, so re-ingesting it into PACS updates the existing study instead of
# minting a duplicate with a fresh StudyInstanceUID.
STUDY_UID_ROOT = "1.2.826.0.1.3680043.2.1125"

# Fixed per-modality UID component. Hashing the modality string (as before) draws from a value
# space far larger than the handful of modalities PI-CAI actually uses, but "far larger" is not
# "collision-free": two of the five in practice could still land on the same digits, which a PACS
# would then merge into one interleaved series. A fixed map removes the collision outright rather
# than just making it rarer. Raise on an unrecognised modality instead of falling back to a shared
# bucket, which would silently reintroduce the same collision for whatever wasn't mapped.
MODALITY_UID_COMPONENT = {
    "t2w": "1",
    "adc": "2",
    "hbv": "3",
    "sag": "4",  # Sagittal T2W — PI-CAI ships this for some studies alongside axial t2w.
    "cor": "5",  # Coronal T2W — likewise.
}


def _deterministic_digits(*parts: str, length: int) -> str:
    """Derive a fixed-length decimal digit string from ``parts`` via SHA-256.

    Unlike Python's builtin ``hash()``, SHA-256 is not salted per interpreter run, so the same
    ``parts`` always produce the same digits — on any machine, on any day.

    Args:
        parts: Strings to combine into the digest input. Joined with a separator so that, e.g.,
            ``("ab", "c")`` and ``("a", "bc")`` never collide.
        length: Number of decimal digits to return.

    Returns:
        A ``length``-digit decimal string derived from the SHA-256 digest of ``parts``.
    """
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return str(int(digest, 16) % 10**length).zfill(length)


# Acquisition metadata PI-CAI leaves in the .mha headers (written by its anonymisation script).
# The marksheet carries no scanner columns, so these headers are the dataset's only per-study
# record of which scanner acquired a scan — carry them into the DICOM instead of dropping them.
PRESERVED_SOURCE_TAGS = (
    "0008|0070",  # Manufacturer
    "0008|1090",  # Manufacturer's Model Name
    "0010|0040",  # Patient's Sex
    "0010|1010",  # Patient's Age
    "0012|0062",  # Patient Identity Removed
)


def load_centers(marksheet_path: Path) -> dict[tuple[str, str], str]:
    """Map each ``(patient_id, study_id)`` to the center that acquired it.

    The acquiring center is the one piece of provenance PI-CAI keeps in the marksheet rather than
    the ``.mha`` headers, and it is what a per-site partition keys on. Carrying it into
    ClinicalTrialSiteID means the contributing center travels with the study into PACS and XNAT,
    so downstream steps read it off the DICOM instead of re-joining the marksheet.

    Args:
        marksheet_path: ``clinical_information/marksheet.csv`` from the PI-CAI labels archive.

    Returns:
        dict[tuple[str, str], str]: ``(patient_id, study_id) -> center``. Empty if the marksheet is
        absent, which leaves InstitutionName unset rather than failing the conversion.
    """
    if not marksheet_path.is_file():
        print(f"⚠️  {marksheet_path} not found — converting without InstitutionName.")
        return {}
    with open(marksheet_path, newline="") as handle:
        return {(row["patient_id"], row["study_id"]): row["center"] for row in csv.DictReader(handle)}


def write_dicom_series(
    image: sitk.Image, out_dir: Path, patient_id: str, study_id: str, modality: str, center: str = ""
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Read the source metadata *before* casting: sitk.Cast returns a new image with an empty
    # metadata dictionary, so anything read after this line would silently come back missing.
    source_tags = {tag: image.GetMetaData(tag) for tag in PRESERVED_SOURCE_TAGS if image.HasMetaDataKey(tag)}
    # PI-CAI writes the acquisition date as YYYY-MM-DD; DICOM DA wants YYYYMMDD. Without this
    # every study is stamped with the date it happened to be converted.
    source_study_date = image.GetMetaData("0008|0020").replace("-", "") if image.HasMetaDataKey("0008|0020") else ""
    image = sitk.Cast(image, sitk.sitkInt16)

    modification_date = time.strftime("%Y%m%d")
    modification_time = time.strftime("%H%M%S")
    if modality not in MODALITY_UID_COMPONENT:
        raise ValueError(f"Unrecognised modality {modality!r}; add it to MODALITY_UID_COMPONENT")
    study_uid = f"{STUDY_UID_ROOT}.{_deterministic_digits(patient_id, study_id, length=10)}"
    series_uid = f"{study_uid}.{MODALITY_UID_COMPONENT[modality]}"
    frame_of_reference_uid = f"{series_uid}.1"

    direction = image.GetDirection()
    orientation = "\\".join(
        str(v) for v in (direction[0], direction[3], direction[6], direction[1], direction[4], direction[7])
    )
    spacing = image.GetSpacing()

    series_tag_values = {
        "0008|0050": f"{patient_id}_{study_id}",
        "0008|0060": "MR",
        "0008|0020": source_study_date or modification_date,
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
    series_tag_values.update(source_tags)
    if center:
        # ClinicalTrialSiteID, NOT InstitutionName (0008,0080). PI-CAI's `center` is a
        # contributing cohort, not the institution that owns the scanner: the 1500 public cases come
        # from 11 sites across these 3 centers, PCNN is a regional network (Prostaat Centrum
        # Noord-Nederland) whose studies span 6 scanner models and both vendors, and ZGT is a
        # hospital group. InstitutionName is defined as where the equipment is located, so it would
        # be a false claim for those two. The 0012 group is the research/de-identification family
        # the .mha headers already use (0012|0062, Patient Identity Removed).
        series_tag_values["0012|0030"] = center  # Clinical Trial Site ID

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


def _convert_one(mha_path: Path, output_dir: Path, centers: dict[tuple[str, str], str]) -> None:
    patient_id, study_id, modality = mha_path.stem.rsplit("_", 2)
    series_dir = output_dir / patient_id / study_id / modality
    image = sitk.ReadImage(str(mha_path))
    write_dicom_series(
        image, series_dir, patient_id, study_id, modality, centers.get((patient_id, study_id), "")
    )


def convert_archive(input_dir: Path, output_dir: Path, workers: int, marksheet_path: Path) -> None:
    mha_paths = sorted(input_dir.rglob("*.mha"))
    centers = load_centers(marksheet_path)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_convert_one, mha_path, output_dir, centers) for mha_path in mha_paths]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Converting to DICOM", unit="scan"):
            future.result()


if __name__ == "__main__":
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    parser = argparse.ArgumentParser(description="Convert PI-CAI .mha scans to DICOM series")
    parser.add_argument("--input", type=Path, default=default_data_dir / "images")
    parser.add_argument("--output", type=Path, default=default_data_dir / "dicom")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument(
        "--marksheet",
        type=Path,
        default=default_data_dir / "clinical_information" / "marksheet.csv",
        help="Marksheet supplying the acquiring center, written to InstitutionName (0008,0080).",
    )
    args = parser.parse_args()
    convert_archive(args.input, args.output, args.workers, args.marksheet)
