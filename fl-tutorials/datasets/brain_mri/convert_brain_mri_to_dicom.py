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

"""MSD Task01 4-D volumes → one MR study per case, four series (FLAIR, T1w, T1Gd, T2w), flat per accession.

Each MSD case is a 240×240×155×4 NIfTI: the four co-registered channels ``dataset.json`` declares. A
hospital PACS holds them as four series of one study, which is what a trust's Orthanc receives here:
one ``StudyInstanceUID`` and ``FrameOfReferenceUID``, ``SeriesNumber`` 1–4 in channel order, and
``SeriesDescription`` = ``ProtocolName`` = the channel label, so that after an XNAT pull dcm2niix's
default filename carries it (``input_FLAIR_<datetime>_1.nii.gz``) and an app can pick a channel by name.

The output is ``<out-dir>/<AccessionNumber>/<SOPInstanceUID>.dcm``, every instance of the study directly
under its accession — the layout ``trust/orthanc/seed_orthanc.py`` seeds from and ``publish_dicom.py``
verifies. Nothing in it depends on the run: identities come from ``utils.synthetic_identity`` and UIDs
from ``utils.dicom_writer``, both pure functions of the case id, so the set regenerates byte-for-byte
from the MSD tar and is never re-hosted (only its OMOP tables are published).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from tqdm import tqdm
from utils import synthetic_identity
from utils.dicom_writer import SeriesTags, StudyTags, write_series

from brain_mri.cases import select_cases

PROJECT = "brain_mri_project"
# The LOINC name of the procedure the OMOP export records (24587-8), so the DICOM and the tables agree;
# omop_convert_brain_mri.py looks this up, lower-cased, in MAPPING_PROCEDURE_TYPE.
STUDY_DESCRIPTION = "MR Brain WO and W contrast IV"
BODY_PART_EXAMINED = "BRAIN"
MODALITY = "MR"
DEFAULT_IMAGES_DIR = Path("data/Task01_BrainTumour/imagesTr")
DEFAULT_DATASET_JSON = Path("data/Task01_BrainTumour/dataset.json")
DEFAULT_OUT_DIR = Path("data/brain_mri/dicom")


@dataclass(frozen=True)
class ChannelSpec:
    """How one MSD channel is described as an MR series: its label and plausible acquisition parameters."""

    label: str
    scanning_sequence: list[str]
    sequence_variant: list[str]
    scan_options: list[str]
    echo_time: float
    repetition_time: float
    inversion_time: float | None = None
    contrast_bolus_agent: str | None = None

    def tags(self) -> dict[str, Any]:
        tags: dict[str, Any] = {
            "ScanningSequence": self.scanning_sequence,
            "SequenceVariant": self.sequence_variant,
            "ScanOptions": self.scan_options,
            "MRAcquisitionType": "3D",
            "EchoTime": self.echo_time,
            "RepetitionTime": self.repetition_time,
            "EchoTrainLength": 1,
        }
        if self.inversion_time is not None:
            tags["InversionTime"] = self.inversion_time
        if self.contrast_bolus_agent is not None:
            tags["ContrastBolusAgent"] = self.contrast_bolus_agent
        return tags


# Keyed by the channel names exactly as MSD's dataset.json spells them.
CHANNELS: dict[str, ChannelSpec] = {
    "FLAIR": ChannelSpec(
        "FLAIR", ["SE", "IR"], ["SK", "SP"], ["IR"], echo_time=90.0, repetition_time=9000.0, inversion_time=2500.0
    ),
    "T1w": ChannelSpec(
        "T1w", ["GR", "IR"], ["SP", "MP"], ["FS"], echo_time=3.0, repetition_time=2300.0, inversion_time=900.0
    ),
    "t1gd": ChannelSpec(
        "T1Gd",
        ["GR", "IR"],
        ["SP", "MP"],
        ["FS"],
        echo_time=3.0,
        repetition_time=2300.0,
        inversion_time=900.0,
        contrast_bolus_agent="Gadolinium",
    ),  # fmt: skip
    "T2w": ChannelSpec("T2w", ["SE"], ["SK"], ["FS"], echo_time=100.0, repetition_time=6000.0),
}


def read_modalities(dataset_json: Path) -> list[str]:
    """The channel names in channel order, from ``dataset.json``'s ``modality`` map (``{"0": "FLAIR", …}``)."""
    modality = json.loads(Path(dataset_json).read_text())["modality"]
    names = [modality[key] for key in sorted(modality, key=int)]
    if unknown := [name for name in names if name not in CHANNELS]:
        raise SystemExit(f"❌ {dataset_json} declares channel(s) this converter has no series spec for: {unknown}")
    return names


def _selected(name: str, channels: list[str] | None) -> bool:
    if channels is None:
        return True
    wanted = {c.lower() for c in channels}
    return name.lower() in wanted or CHANNELS[name].label.lower() in wanted


def convert_case(
    case: str, images_dir: Path, modalities: list[str], out_dir: Path, channels: list[str] | None = None
) -> Path:
    """One MSD case → ``<out_dir>/<accession>/*.dcm``: one MR study, one series per (selected) channel.

    Args:
        case: The MSD case id, e.g. ``BRATS_001``.
        images_dir: The ``imagesTr`` directory holding ``<case>.nii.gz``.
        modalities: Channel names in channel order (:func:`read_modalities`).
        out_dir: Root of the DICOM tree; the case's accession directory under it is replaced.
        channels: Channel names or labels to keep (``["FLAIR", "T1Gd"]``); ``None`` keeps all four.

    Returns:
        Path: The accession directory.

    Raises:
        SystemExit: If the volume is not 4-D with one channel per declared modality.
    """
    image = nib.load(str(Path(images_dir) / f"{case}.nii.gz"))
    volume = image.get_fdata(dtype=np.float32)
    if volume.ndim != 4 or volume.shape[3] != len(modalities):
        raise SystemExit(
            f"❌ {case}: expected a 4-D volume with {len(modalities)} channel(s) ({modalities}), "
            f"got shape {volume.shape}"
        )

    who = synthetic_identity.identity(case)
    scanner = synthetic_identity.scanner(case, MODALITY)
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
        extra={"ClinicalTrialSubjectID": case},
    )
    accession_dir = Path(out_dir) / who.accession_number
    shutil.rmtree(accession_dir, ignore_errors=True)
    for index, name in enumerate(modalities):
        if not _selected(name, channels):
            continue
        spec = CHANNELS[name]
        series = SeriesTags(
            modality=MODALITY,
            series_number=index + 1,
            series_description=spec.label,
            protocol_name=spec.label,
            body_part_examined=BODY_PART_EXAMINED,
            manufacturer=scanner.manufacturer,
            manufacturer_model_name=scanner.model,
            extra={"MagneticFieldStrength": scanner.field_strength, **spec.tags()},
        )
        write_series(
            volume[..., index], image.affine, accession_dir, study=study, series=series, uid_entropy=[PROJECT, case]
        )
    return accession_dir


def _convert_one(args: tuple[str, Path, list[str], Path, list[str] | None]) -> tuple[str, Path]:
    case, images_dir, modalities, out_dir, channels = args
    return case, convert_case(case, images_dir, modalities, out_dir, channels)


def convert(
    images_dir: Path,
    dataset_json: Path,
    out_dir: Path,
    cases: list[str],
    channels: list[str] | None = None,
    workers: int = 1,
) -> dict[str, Path]:
    """Convert ``cases``; returns ``{case: accession directory}``. ``workers > 1`` converts cases in parallel."""
    modalities = read_modalities(dataset_json)
    jobs = [(case, Path(images_dir), modalities, Path(out_dir), channels) for case in cases]
    written: dict[str, Path] = {}
    if workers > 1:
        # spawn, not fork: nibabel/numpy may hold locks in this (possibly multi-threaded) process.
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            for case, accession_dir in tqdm(pool.map(_convert_one, jobs), total=len(jobs), desc="cases"):
                written[case] = accession_dir
    else:
        for job in tqdm(jobs, desc="cases"):
            case, accession_dir = _convert_one(job)
            written[case] = accession_dir
    print(f"✅ {len(written)} case(s) → {out_dir}", flush=True)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images-dir", type=Path, default=DEFAULT_IMAGES_DIR)
    parser.add_argument("--dataset-json", type=Path, default=DEFAULT_DATASET_JSON)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--num-cases", type=int, help="the N lowest-numbered cases (default: all)")
    which.add_argument("--metadata-table", type=Path, help="exactly the cases a published metadata table names")
    parser.add_argument("--channels", nargs="+", help="channel names/labels to convert (default: all four)")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)

    cases = select_cases(args.images_dir, args.num_cases, args.metadata_table)
    convert(args.images_dir, args.dataset_json, args.out_dir, cases, args.channels, args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
