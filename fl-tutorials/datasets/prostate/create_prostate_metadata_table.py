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

"""DICOM tree → the metadata table the prostate OMOP conversion is built from (FLIP#1100 follow-on).

One row per **series** (``<patient>/<study>/<modality>/``), read from the header of the series'
first instance: identity, study/series UIDs, the acquisition metadata PI-CAI keeps in its headers
(``Manufacturer``, ``ManufacturersModelName``, ``PatientSex``, ``PatientAge``), the geometry, the
contributing center (``ClinicalTrialSiteID``, from the marksheet via ``convert_mha_to_dicom.py``) and
the instance count. Written to ``source/dicom_metadata.csv`` beside a copy of the marksheet trimmed
to the studies present (``source/marksheet.csv``); both are published with the OMOP tables so the
conversion is reproducible from the published inputs alone, as spleen's and cxr's are.

**``source_trust`` is decided here, from the scanner vendor.** The dev stack has two trusts and
PI-CAI three centers, so the partition that makes a realistic two-site federation is the one real
domain shift in the data: Siemens (RUMC, ZGT and a few PCNN studies) → trust 1, Philips (the rest of
PCNN) → trust 2. Every series of a study — and every study of a patient (no patient in the public
set was scanned on both vendors) — lands on one trust. A manufacturer this table does not know is an
error, not a silent third trust.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pydicom

# Vendor → trust slot. Keys are the exact Manufacturer (0008,0070) values PI-CAI's headers carry.
SOURCE_TRUST_BY_MANUFACTURER = {
    "SIEMENS": 1,
    "Philips Medical Systems": 2,
}

# Keyword → column. Read once per series from its first instance; per-instance tags are not here.
HEADER_KEYWORDS = (
    "PatientID",
    "PatientSex",
    "PatientAge",
    "StudyDate",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SeriesDescription",
    "AccessionNumber",
    "Modality",
    "Manufacturer",
    "ManufacturerModelName",
    "SliceThickness",
    "Rows",
    "Columns",
    "PixelSpacing",
    "ClinicalTrialSiteID",
)
COLUMNS = ("patient_id", "study_id", "modality", *HEADER_KEYWORDS, "NumberOfInstances", "source_trust")


def source_trust_for(manufacturer: str) -> int:
    """The trust slot a series belongs to, from its scanner vendor."""
    try:
        return SOURCE_TRUST_BY_MANUFACTURER[manufacturer.strip()]
    except KeyError:
        raise SystemExit(
            f"Manufacturer {manufacturer!r} has no trust in SOURCE_TRUST_BY_MANUFACTURER "
            f"({sorted(SOURCE_TRUST_BY_MANUFACTURER)}) — add it deliberately rather than guessing a slot"
        ) from None


def _value(ds: pydicom.Dataset, keyword: str) -> str:
    """A header value as text — multi-valued tags joined with backslashes, absent tags empty."""
    value = ds.get(keyword, "")
    if value is None or value == "":
        return ""
    if isinstance(value, pydicom.multival.MultiValue):
        return "\\".join(str(v) for v in value)
    return str(value)


def series_row(series_dir: Path) -> dict[str, str]:
    """One metadata row for the series in ``<patient>/<study>/<modality>/``."""
    instances = sorted(series_dir.glob("*.dcm"))
    if not instances:
        raise SystemExit(f"{series_dir}: no .dcm files")
    ds = pydicom.dcmread(instances[0], stop_before_pixels=True)
    row = {
        "patient_id": series_dir.parents[1].name,
        "study_id": series_dir.parent.name,
        "modality": series_dir.name,
    }
    row.update({keyword: _value(ds, keyword) for keyword in HEADER_KEYWORDS})
    row["NumberOfInstances"] = str(len(instances))
    row["source_trust"] = str(source_trust_for(row["Manufacturer"]))
    return row


def build_table(dicom_dir: Path) -> list[dict[str, str]]:
    """Every series under ``dicom_dir``, in path order."""
    rows = [series_row(d) for d in sorted(dicom_dir.glob("*/*/*")) if d.is_dir()]
    if not rows:
        raise SystemExit(f"no <patient>/<study>/<modality>/ series under {dicom_dir} — run convert_mha_to_dicom.py")
    return rows


def trimmed_marksheet(marksheet: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The marksheet rows for exactly the studies in the table, in marksheet order."""
    present = {(r["patient_id"], r["study_id"]) for r in rows}
    with marksheet.open(newline="") as handle:
        kept = [r for r in csv.DictReader(handle) if (r["patient_id"], r["study_id"]) in present]
    missing = present - {(r["patient_id"], r["study_id"]) for r in kept}
    if missing:
        raise SystemExit(f"{len(missing)} study(ies) in the DICOM tree are not in {marksheet}: {sorted(missing)[:5]}")
    return kept


def write_csv(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...] | list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    parser = argparse.ArgumentParser(description="One metadata row per DICOM series, plus the trimmed marksheet")
    parser.add_argument("--dicom", type=Path, default=default_data_dir / "dicom")
    parser.add_argument("--marksheet", type=Path, default=default_data_dir / "clinical_information" / "marksheet.csv")
    parser.add_argument("--output", type=Path, default=default_data_dir / "source", help="directory for the two CSVs")
    args = parser.parse_args(argv)

    rows = build_table(args.dicom)
    marksheet_rows = trimmed_marksheet(args.marksheet, rows)
    write_csv(args.output / "dicom_metadata.csv", rows, COLUMNS)
    write_csv(args.output / "marksheet.csv", marksheet_rows, list(marksheet_rows[0].keys()))
    studies = {(r["patient_id"], r["study_id"]) for r in rows}
    by_trust = {t: len({(r["patient_id"], r["study_id"]) for r in rows if r["source_trust"] == t}) for t in ("1", "2")}
    print(f"✅ {len(rows)} series / {len(studies)} studies → {args.output} (source_trust studies: {by_trust})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
