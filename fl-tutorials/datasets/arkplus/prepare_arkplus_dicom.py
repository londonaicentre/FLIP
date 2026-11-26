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

"""The Ark+ download → the DICOM set a trust's PACS is seeded with (FLIP#2084).

The downloaded DICOMs leave out three tags the platform needs once a study is in a PACS: imaging-api
drops a study without ``StudyDescription`` or ``ReferringPhysicianName`` from XNAT's query response
("No studies found to import"), and its patient model requires ``PatientSex``. This fills the three
when they are empty — the sex from ``source/dicom_metadata.csv``, so the PACS agrees with OMOP — and
changes nothing else. The pre-#2084 loader made the same fills on upload; here they are made once,
before ``publish_dicom.py`` verifies and packages the set, so the seeder uploads what was published.

Output: ``<out>/<AccessionNumber>/<SOPInstanceUID>.dcm``, one instance per accession.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pydicom

STUDY_DESCRIPTION = "Chest X-ray"
REFERRING_PHYSICIAN = "Synthetic^Referring"


def fill_required_tags(ds: pydicom.Dataset, sex: str) -> list[str]:
    """Fill the tags the platform requires where the generator left them empty. Returns their names."""
    filled = []
    for keyword, value in (
        ("StudyDescription", STUDY_DESCRIPTION),
        ("ReferringPhysicianName", REFERRING_PHYSICIAN),
        ("PatientSex", sex),
    ):
        if not str(ds.get(keyword, "") or "").strip():
            setattr(ds, keyword, value)
            filled.append(keyword)
    return filled


def main(argv: list[str] | None = None) -> int:
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "arkplus"
    parser = argparse.ArgumentParser(description="Write the Ark+ DICOM set with the platform's required tags filled")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir, help="holds site1/ … site2_holdoff/")
    parser.add_argument("--metadata", type=Path, default=default_data_dir / "source" / "dicom_metadata.csv")
    parser.add_argument("--out", type=Path, default=default_data_dir / "dicom")
    args = parser.parse_args(argv)

    with args.metadata.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for i, row in enumerate(rows, 1):
        name = row["source_name"]
        ds = pydicom.dcmread(args.data_dir / row["folder"] / "accession-resources" / name / f"{name}.dcm")
        if str(ds.AccessionNumber) != row["AccessionNumber"]:
            raise SystemExit(f"{name}: AccessionNumber {ds.AccessionNumber} differs from the metadata table's")
        fill_required_tags(ds, row["PatientSex"])
        out = args.out / row["AccessionNumber"] / f"{ds.SOPInstanceUID}.dcm"
        out.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(out, enforce_file_format=True)
        if i % 1000 == 0 or i == len(rows):
            print(f"  wrote {i}/{len(rows)}")
    print(f"✅ {len(rows)} DICOMs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
