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

"""DICOM tree → the metadata table the brain_mri OMOP conversion is built from.

One row per **series** (a study has four), read from the header of each instance and grouped by
``SeriesInstanceUID``; a fixed column list rather than spleen's every-tag dump, so the table's shape
is part of the contract. Written to ``source/dicom_metadata.csv`` and published beside the OMOP tables:
it is the one input the conversion reproduces from, and — since the DICOMs themselves are never
published — the record of exactly which studies the regenerated set must contain (``cases.py``
``--metadata-table``).

**``source_trust`` is decided here**, round-robin over the cases in natural order (odd → trust 1, even
→ trust 2), every series of a study on one trust. ``seed_orthanc.py`` and ``omop_db_tools`` both key on
the same column, so a trust's PACS studies and its OMOP rows agree by construction.

Quality gates, loud rather than silent: one patient id and one accession per case, the expected number
of series per study, and at least ``COHORT_QUERY_THRESHOLD`` (10) studies per trust — the data-access
API refuses a smaller cohort, so a trust below the floor could never be pulled.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import pydicom
from natsort import natsorted

COLUMNS = [
    "Subject",
    "PatientID",
    "PatientName",
    "PatientSex",
    "PatientBirthDate",
    "AccessionNumber",
    "StudyDate",
    "StudyTime",
    "StudyDescription",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SeriesNumber",
    "SeriesDescription",
    "Modality",
    "Manufacturer",
    "ManufacturerModelName",
    "MagneticFieldStrength",
    "SliceThickness",
    "Rows",
    "Columns",
    "PixelSpacing",
    "NumberOfInstances",
    "source_trust",
]
# Read from the header: every column except the two derived ones.
HEADER_KEYWORDS = [c for c in COLUMNS if c not in ("Subject", "NumberOfInstances", "source_trust")]
SUBJECT_KEYWORD = "ClinicalTrialSubjectID"  # (0012,0040): the converter stamps the MSD case id here
DEFAULT_DICOM_DIR = Path("data/brain_mri/dicom")
DEFAULT_OUTPUT = Path("data/brain_mri/source/dicom_metadata.csv")
COHORT_QUERY_THRESHOLD = 10


def _value(ds: pydicom.Dataset, keyword: str) -> str:
    """A header value as text — multi-valued tags joined with backslashes, absent tags empty."""
    value = ds.get(keyword, "")
    if value is None or value == "":
        return ""
    if isinstance(value, pydicom.multival.MultiValue):
        return "\\".join(str(v) for v in value)
    return str(value)


def read_series(dicom_dir: Path) -> list[dict[str, str]]:
    """One row per series under ``<dicom_dir>/<accession>/*.dcm``, headers only.

    Raises:
        SystemExit: If there are no instances, or an instance carries no ``ClinicalTrialSubjectID``.
    """
    rows: dict[str, dict[str, str]] = {}
    counts: Counter[str] = Counter()
    accession_dirs = natsorted((p for p in Path(dicom_dir).iterdir() if p.is_dir()), key=lambda p: p.name)
    for accession_dir in accession_dirs:
        for path in sorted(accession_dir.glob("*.dcm")):
            ds = pydicom.dcmread(str(path), stop_before_pixels=True)
            series_uid = str(ds.SeriesInstanceUID)
            counts[series_uid] += 1
            if series_uid in rows:
                continue
            subject = _value(ds, SUBJECT_KEYWORD)
            if not subject:
                raise SystemExit(f"❌ {path}: no {SUBJECT_KEYWORD} — not written by convert_brain_mri_to_dicom.py?")
            row = {"Subject": subject}
            row.update({keyword: _value(ds, keyword) for keyword in HEADER_KEYWORDS})
            rows[series_uid] = row
    if not rows:
        raise SystemExit(
            f"❌ no DICOM instances under {dicom_dir} — run `make -C fl-tutorials convert-brain-mri-to-dicom` first"
        )
    for series_uid, row in rows.items():
        row["NumberOfInstances"] = str(counts[series_uid])
    return sorted(rows.values(), key=lambda r: (natsorted([r["Subject"]])[0], int(r["SeriesNumber"] or 0)))


def assign_source_trust(rows: list[dict[str, str]], num_trusts: int) -> list[dict[str, str]]:
    """Round-robin over the cases in natural order; every series of a case gets its case's trust."""
    subjects = natsorted({row["Subject"] for row in rows})
    trust_of = {subject: index % num_trusts + 1 for index, subject in enumerate(subjects)}
    for row in rows:
        row["source_trust"] = str(trust_of[row["Subject"]])
    return rows


def quality_checks(rows: list[dict[str, str]], series_per_study: int | None, min_studies_per_trust: int) -> None:
    """Refuse a table the rest of the chain would mis-seed or the platform would refuse to pull."""
    subjects_by_accession: defaultdict[str, set[str]] = defaultdict(set)
    subjects_by_patient: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        subjects_by_accession[row["AccessionNumber"]].add(row["Subject"])
        subjects_by_patient[row["PatientID"]].add(row["Subject"])
    if shared := {a: s for a, s in subjects_by_accession.items() if len(s) > 1}:
        raise SystemExit(f"❌ an accession is shared by more than one case: {shared}")
    if shared := {p: s for p, s in subjects_by_patient.items() if len(s) > 1}:
        raise SystemExit(f"❌ a PatientID is shared by more than one case: {shared}")
    if series_per_study is not None:
        counts = Counter(row["AccessionNumber"] for row in rows)
        if wrong := {a: n for a, n in counts.items() if n != series_per_study}:
            raise SystemExit(f"❌ expected {series_per_study} series per study, but: {wrong}")
    studies_per_trust = Counter()
    for accession, subjects in subjects_by_accession.items():
        trust = next(row["source_trust"] for row in rows if row["AccessionNumber"] == accession)
        studies_per_trust[trust] += 1
    if short := {t: n for t, n in studies_per_trust.items() if n < min_studies_per_trust}:
        raise SystemExit(
            f"❌ trust(s) below the {min_studies_per_trust}-study floor (the trusts' COHORT_QUERY_THRESHOLD): {short}"
        )


def build_table(
    dicom_dir: Path,
    num_trusts: int = 2,
    series_per_study: int | None = 4,
    min_studies_per_trust: int = COHORT_QUERY_THRESHOLD,
) -> pd.DataFrame:
    """The metadata table for a DICOM tree, every column a string, ``COLUMNS`` order."""
    rows = assign_source_trust(read_series(dicom_dir), num_trusts)
    quality_checks(rows, series_per_study, min_studies_per_trust)
    return pd.DataFrame(rows, columns=COLUMNS, dtype=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dicom-dir", type=Path, default=DEFAULT_DICOM_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--num-trusts", type=int, default=2)
    parser.add_argument("--series-per-study", type=int, default=4, help="0 disables the check")
    parser.add_argument("--min-studies-per-trust", type=int, default=COHORT_QUERY_THRESHOLD)
    args = parser.parse_args(argv)

    table = build_table(args.dicom_dir, args.num_trusts, args.series_per_study or None, args.min_studies_per_trust)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output, index=False)
    studies = table.drop_duplicates("AccessionNumber")
    per_trust = studies["source_trust"].value_counts().sort_index().to_dict()
    print(
        f"✅ {len(table)} series / {len(studies)} studies → {args.output} (studies per trust: {per_trust})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
