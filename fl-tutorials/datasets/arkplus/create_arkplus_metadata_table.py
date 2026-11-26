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

"""The Ark+ download → the metadata table the arkplus_project OMOP conversion is built from (FLIP#2084).

Reads the four folders ``download-arkplus-finetuning-data`` and ``download-arkplus-eval-data`` lay out
(``site1``, ``site1_holdoff``, ``site2``, ``site2_holdoff``: one DICOM per accession under
``accession-resources/<id>/<id>.dcm`` and the labels in ``sample_get_dataframe_response.csv``) and
writes one row per accession to ``source/dicom_metadata.csv``: the DICOM's identifying tags, the
folder it came from, the split and the seven label columns. The table is published with the OMOP
tables, so the conversion reproduces from the published inputs alone, as cxr's and prostate's do.

**``source_trust`` is the dataset's own site**: ``site1`` and its holdout → 1, ``site2`` and its holdout
→ 2. Each site is one synthetic cohort, made by a different generator (the preprint's UK and Thai
cohorts), so the split is non-IID by design, unlike ``cxr_project``'s round-robin.

The DICOMs carry no sex or birth date, so both are derived here from the AccessionNumber, with the
same hash the pre-#2084 loader used, so a trust's cohort demographics do not change on re-seeding.
``prepare_arkplus_dicom.py`` stamps the same sex into the DICOM it publishes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

import pydicom

# Folder → (source_trust, split). The split decides the procedure_source_value the cohort queries
# select on: the training query reads "Chest X-ray", both evaluation queries "Chest X-ray (holdout)".
SPLITS = {
    "site1": (1, "train"),
    "site1_holdoff": (1, "holdout"),
    "site2": (2, "train"),
    "site2_holdoff": (2, "holdout"),
}
LABEL_COLUMNS = (
    "Effusion",
    "Edema",
    "Consolidation",
    "Infiltration",
    "Lung Nodule or Mass",
    "Pneumothorax",
    "Lungs in normal arrangement",
)
LABEL_VALUES = {"Yes", "No"}
HEADER_KEYWORDS = (
    "AccessionNumber",
    "PatientID",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "StudyDate",
    "StudyTime",
    "Modality",
)
COLUMNS = (
    "source_name",
    "folder",
    "split",
    "source_trust",
    *HEADER_KEYWORDS,
    "PatientSex",
    "YearOfBirth",
    *LABEL_COLUMNS,
)


def demographics(accession: str) -> tuple[str, int]:
    """A stable synthetic (sex, year of birth) for an accession, as the pre-#2084 loader derived them.

    Args:
        accession: The DICOM AccessionNumber.

    Returns:
        tuple[str, int]: ``("M" | "F", 1940..2005)``.
    """
    digest = hashlib.sha256(accession.encode()).digest()
    return ("M" if digest[0] % 2 == 0 else "F"), 1940 + digest[1] % 66


def folder_rows(folder: Path) -> list[dict[str, str]]:
    """One row per labelled accession of a downloaded site folder, in the label table's order."""
    source_trust, split = SPLITS[folder.name]
    labels_csv = folder / "sample_get_dataframe_response.csv"
    if not labels_csv.is_file():
        raise SystemExit(
            f"{labels_csv} not found — run make -C fl-tutorials download-arkplus-finetuning-data "
            "download-arkplus-eval-data"
        )
    with labels_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if missing := set(LABEL_COLUMNS) - set(reader.fieldnames or ()):
            raise SystemExit(f"{labels_csv}: label column(s) missing: {sorted(missing)}")
        labelled = list(reader)
    rows = []
    for label_row in labelled:
        name = label_row["accession_id"]
        dcm = folder / "accession-resources" / name / f"{name}.dcm"
        if not dcm.is_file():
            raise SystemExit(f"{dcm}: no DICOM for labelled accession {name}")
        if bad := {c: label_row[c] for c in LABEL_COLUMNS if label_row[c] not in LABEL_VALUES}:
            raise SystemExit(f"{labels_csv}: {name} has label values outside {sorted(LABEL_VALUES)}: {bad}")
        ds = pydicom.dcmread(dcm, stop_before_pixels=True)
        header = {keyword: str(ds.get(keyword, "") or "") for keyword in HEADER_KEYWORDS}
        if not header["AccessionNumber"]:
            raise SystemExit(f"{dcm}: no AccessionNumber, which is what the trust keys the study on")
        sex, year_of_birth = demographics(header["AccessionNumber"])
        rows.append(
            {
                "source_name": name,
                "folder": folder.name,
                "split": split,
                "source_trust": str(source_trust),
                **header,
                "PatientSex": sex,
                "YearOfBirth": str(year_of_birth),
                **{c: label_row[c] for c in LABEL_COLUMNS},
            }
        )
    return rows


def build_table(data_dir: Path) -> list[dict[str, str]]:
    """Every accession of the four site folders, in ``SPLITS`` order.

    Raises:
        SystemExit: If a folder is missing or an AccessionNumber repeats — it is the study's key on
            every trust, so two images sharing one would be one study to the platform.
    """
    rows = []
    for name in SPLITS:
        folder = data_dir / name
        if not folder.is_dir():
            raise SystemExit(f"{folder} not found — download both the training and the holdout splits first")
        rows.extend(folder_rows(folder))
    seen: dict[str, str] = {}
    for row in rows:
        if (first := seen.setdefault(row["AccessionNumber"], row["source_name"])) != row["source_name"]:
            raise SystemExit(f"AccessionNumber {row['AccessionNumber']} is on both {first} and {row['source_name']}")
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "arkplus"
    parser = argparse.ArgumentParser(description="One metadata row per Ark+ accession, from the four site folders")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir, help="holds site1/ … site2_holdoff/")
    parser.add_argument("--output", type=Path, default=default_data_dir / "source" / "dicom_metadata.csv")
    args = parser.parse_args(argv)

    rows = build_table(args.data_dir)
    write_csv(args.output, rows)
    counts = {name: sum(r["folder"] == name for r in rows) for name in SPLITS}
    print(f"✅ {len(rows)} accessions → {args.output} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
