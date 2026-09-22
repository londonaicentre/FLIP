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

"""Convert the MSD spleen NIfTI volumes to DICOM, mocking the tags a PACS expects.

Imported from ``londonaicentre/flip_project_spleen_segmentation`` (FLIP#1092) and rewritten on the
shared writer in FLIP#1221. The original called ``plastimatch convert`` for the slicing and then a
second pydicom pass to stamp the tags plastimatch's wrapper cannot set; plastimatch needed root to
install its binary (so this step was never in CI) and minted fresh UIDs on every run. It now runs
anywhere, needs nothing but this project's venv, and re-running it writes the same bytes: every UID
and identity is a function of the subject id (``utils.dicom_writer``, ``utils.synthetic_identity``).

Same contract as before for the rest of the chain: reads ``data/Task09_Spleen/imagesTr`` and writes
``dicom_output/<subject>/*.dcm``, one CT series per subject, run from ``fl-tutorials/``
(``make -C fl-tutorials convert-spleen-to-dicom``). The published spleen DICOM set was produced by the
plastimatch version, so this output still differs from it — the verification gate never compared
regenerated DICOMs, only OMOP tables built from the *published* metadata table.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
from utils import synthetic_identity
from utils.dicom_writer import SeriesTags, StudyTags, write_series

INPUT_DIR = Path("data/Task09_Spleen/imagesTr")
OUTPUT_DIR = Path("dicom_output")
PROJECT = "spleen_project"
# StudyDescription is what omop_convert_spleen.py looks up (lower-cased) in MAPPING_PROCEDURE_TYPE, and
# SeriesDescription doubles as ProtocolName, which dcm2niix puts in the NIfTI filename after a pull
# (input_CT_Spleen_<datetime>_<series>.nii.gz — the name the spleen apps' label upload derives from).
STUDY_DESCRIPTION = "Spleen CT"
SERIES_DESCRIPTION = "CT Spleen"
MODALITY = "CT"
BODY_PART_EXAMINED = "ABDOMEN"
KVP = 120.0


def subject_of(nifti: Path) -> str:
    """``spleen_10`` from ``spleen_10.nii.gz``."""
    return nifti.name.removesuffix(".nii.gz")


def convert_subject(nifti: Path, output_dir: Path) -> list[Path]:
    """One MSD volume → ``<output_dir>/<subject>/*.dcm``, a single CT series with a synthetic patient.

    Args:
        nifti: The ``imagesTr/<subject>.nii.gz`` volume.
        output_dir: Root of the DICOM tree; the subject's directory under it is replaced.

    Returns:
        list[Path]: The written instances, slice order.
    """
    subject = subject_of(nifti)
    image = nib.load(str(nifti))
    volume = image.get_fdata(dtype=np.float32)  # HU, as MSD stores them

    who = synthetic_identity.identity(subject)
    scanner = synthetic_identity.scanner(subject, MODALITY)
    study = StudyTags(
        patient_id=who.patient_id,
        patient_name=who.patient_name,
        patient_sex=who.sex,
        patient_birth_date=who.birth_date.strftime("%Y%m%d"),
        accession_number=who.accession_number,
        study_date=who.study_datetime.strftime("%Y%m%d"),
        study_time=who.study_datetime.strftime("%H%M%S"),
        study_description=STUDY_DESCRIPTION,
        referring_physician_name=who.referring_physician,
        institution_name=who.institution,
        department_name=who.department,
        extra={"ClinicalTrialSubjectID": subject},
    )
    series = SeriesTags(
        modality=MODALITY,
        series_number=1,
        series_description=SERIES_DESCRIPTION,
        protocol_name=SERIES_DESCRIPTION,
        body_part_examined=BODY_PART_EXAMINED,
        manufacturer=scanner.manufacturer,
        manufacturer_model_name=scanner.model,
        extra={"KVP": KVP},
    )

    subject_dir = output_dir / subject
    shutil.rmtree(subject_dir, ignore_errors=True)
    return write_series(volume, image.affine, subject_dir, study=study, series=series, uid_entropy=[PROJECT, subject])


def convert_dataset(input_dir: Path, output_dir: Path) -> dict[str, list[Path]]:
    """Every ``*.nii.gz`` under ``input_dir`` (macOS ``._`` resource forks skipped), in name order.

    Raises:
        SystemExit: If ``input_dir`` holds no volumes — a missing download must not look like a run.
    """
    volumes = sorted(p for p in Path(input_dir).glob("*.nii.gz") if not p.name.startswith("._"))
    if not volumes:
        raise SystemExit(
            f"❌ no .nii.gz volumes under {input_dir} — run `make -C fl-tutorials download-spleen-msd-raw` first"
        )
    written: dict[str, list[Path]] = {}
    for nifti in volumes:
        print(f"Converting {nifti.name} …", flush=True)
        written[subject_of(nifti)] = convert_subject(nifti, Path(output_dir))
    print(f"✅ {len(written)} subject(s) → {output_dir}")
    return written


if __name__ == "__main__":
    convert_dataset(INPUT_DIR, OUTPUT_DIR)
    sys.exit(0)
