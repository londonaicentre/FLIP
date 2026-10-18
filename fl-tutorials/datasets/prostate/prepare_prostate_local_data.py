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

"""The simulator layout: what the prostate tutorial reads under ``LOCAL_DEV`` in place of an XNAT pull.

On the platform an fl-client asks imaging-api for one accession and gets XNAT's export tree back:
``<accession>/<session>/scans/<scan id>-<description>/resources/NIFTI/files/input_<…>.nii.gz``, one
scan per series (1 = T2W, 2 = ADC, 3 = high-b DWI — the SeriesNumbers ``convert_mha_to_dicom.py``
stamps), and after enrichment a ``label_<…>`` and ``zonal_<…>`` mask beside every scan's image. This
script writes that shape straight from the PI-CAI download, so the simulator exercises the same
series selection and mask pairing as the platform:

    data/prostate/images/<accession>/scans/<n>-<Description>/{input,label,zonal}_<accession>_<mod>.nii.gz
    data/prostate/dataframe.csv        one row per SERIES (three per study), the columns query.sql projects

The image is written by SimpleITK from the ``.mha``, not through dcm2niix: the two store voxels in
different row orders, which the tutorial's loader reconciles by affine either way
(``app/dataset.build_loader``; ``tests/test_prostate_dataset_orientation.py`` pins both), so no
Docker is needed here. ``--site-tree`` additionally lays the same files out per contributing centre
in the ``PicaiDataset`` layout the nnU-Net planner reads, for a plan without the dcm2niix chain.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

import pandas as pd
import SimpleITK as sitk
from convert_mha_to_dicom import MODALITY_DESCRIPTIONS, MODALITY_UID_COMPONENT, UID_PREFIX
from omop_convert_prostate import max_pirads
from pydicom.uid import generate_uid
from tqdm import tqdm

MODALITIES = ("t2w", "adc", "hbv")
DEFAULT_DATA_DIR = Path("data/prostate")
# query.sql's projection, so the simulator's dataframe has the platform's columns.
DATAFRAME_COLUMNS = [
    "accession_id",
    "image_series_uid",
    "Image date",
    "Modality",
    "Anatomy",
    "ISUP grade group",
    "Clinically significant cancer",
    "PI-RADS",
]
MODALITY_CONCEPT_NAME = "Magnetic resonance imaging"
ANATOMY_CONCEPT_NAME = "Prostatic structure"


def scan_dirname(modality: str) -> str:
    """``1-T2_Weighted``: XNAT's ``<scan id>-<series description>`` folder, spaces underscored."""
    return f"{MODALITY_UID_COMPONENT[modality]}-{MODALITY_DESCRIPTIONS[modality].replace(' ', '_')}"


def series_uid(patient_id: str, study_id: str, modality: str) -> str:
    """The SeriesInstanceUID the DICOM set carries — the same deterministic derivation as the writer's."""
    return generate_uid(prefix=UID_PREFIX, entropy_srcs=[patient_id, study_id, MODALITY_UID_COMPONENT[modality]])


def select_cases(images_dir: Path, labels_dir: Path, zonal_dir: Path, num_cases: int | None = None) -> list[str]:
    """Accessions with all three ``.mha`` scans and both masks, sorted; the first ``num_cases`` of them."""
    cases: list[str] = []
    for patient_dir in sorted(p for p in Path(images_dir).iterdir() if p.is_dir()):
        for t2w in sorted(patient_dir.glob(f"{patient_dir.name}_*_t2w.mha")):
            accession = t2w.name.removesuffix("_t2w.mha")
            scans = all((patient_dir / f"{accession}_{m}.mha").is_file() for m in MODALITIES)
            masks = (Path(labels_dir) / f"{accession}.nii.gz").is_file() and (
                Path(zonal_dir) / f"{accession}.nii.gz"
            ).is_file()
            if scans and masks:
                cases.append(accession)
    return cases[:num_cases] if num_cases else cases


def write_case(
    accession: str, images_dir: Path, labels_dir: Path, zonal_dir: Path, out_images: Path
) -> dict[str, Path]:
    """One study: three scan folders, each with the image and both masks. Returns the image per modality."""
    patient_id = accession.split("_", 1)[0]
    study = Path(out_images) / accession / "scans"
    shutil.rmtree(study.parent, ignore_errors=True)
    written: dict[str, Path] = {}
    for modality in MODALITIES:
        scan = study / scan_dirname(modality)
        scan.mkdir(parents=True)
        image = scan / f"input_{accession}_{modality}.nii.gz"
        sitk.WriteImage(sitk.ReadImage(str(Path(images_dir) / patient_id / f"{accession}_{modality}.mha")), str(image))
        # The enrichment step uploads the same masks into every scan of the accession.
        shutil.copyfile(Path(labels_dir) / f"{accession}.nii.gz", scan / f"label_{accession}_{modality}.nii.gz")
        shutil.copyfile(Path(zonal_dir) / f"{accession}.nii.gz", scan / f"zonal_{accession}_{modality}.nii.gz")
        written[modality] = image
    return written


def _clinical(marksheet: Path | None) -> pd.DataFrame:
    """The marksheet keyed by accession, with the three cohort columns query.sql derives from it."""
    if marksheet is None or not Path(marksheet).is_file():
        return pd.DataFrame(
            columns=["accession_id", "Image date", "ISUP grade group", "Clinically significant cancer", "PI-RADS"]
        )
    sheet = pd.read_csv(marksheet, dtype=str)
    return (
        pd.DataFrame(
            {
                "accession_id": sheet["patient_id"].str.strip() + "_" + sheet["study_id"].str.strip(),
                "Image date": sheet.get("mri_date", pd.Series([""] * len(sheet))).fillna(""),
                "ISUP grade group": pd.to_numeric(sheet.get("case_ISUP"), errors="coerce"),
                "Clinically significant cancer": sheet.get("case_csPCa", pd.Series([""] * len(sheet)))
                .fillna("")
                .str.strip()
                .str.upper()
                .map({"YES": "Yes", "NO": "No"})
                .fillna(""),
                "PI-RADS": sheet.get("lesion_PIRADS", pd.Series([None] * len(sheet))).map(max_pirads),
            }
        )
        .drop_duplicates("accession_id")
        .set_index("accession_id")
    )


def _link_site_tree(
    site_tree: Path, center: str, accession: str, images: dict[str, Path], labels_dir: Path, zonal_dir: Path
) -> None:
    """``PicaiDataset``'s layout (``manifest.csv`` + ``nifti/``, ``labels/``, ``zonal_labels/``) as symlinks."""
    site = Path(site_tree) / center
    for sub in ("nifti", "labels", "zonal_labels"):
        (site / sub).mkdir(parents=True, exist_ok=True)
    for modality, image in images.items():
        link = site / "nifti" / f"{accession}_{modality}.nii.gz"
        link.unlink(missing_ok=True)
        link.symlink_to(image.resolve())
    for sub, source in (("labels", labels_dir), ("zonal_labels", zonal_dir)):
        link = site / sub / f"{accession}.nii.gz"
        link.unlink(missing_ok=True)
        link.symlink_to((Path(source) / f"{accession}.nii.gz").resolve())
    patient_id, study_id = accession.split("_", 1)
    manifest = site / "manifest.csv"
    new = not manifest.exists()
    with manifest.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["patient_id", "study_id"])
        writer.writerow([patient_id, study_id])


def prepare(data_dir: Path, out_dir: Path, cases: list[str], site_tree: Path | None = None) -> list[dict[str, object]]:
    """Write the layout for ``cases``; returns the dataframe rows, also written to ``<out_dir>/dataframe.csv``."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    images_dir, labels_dir, zonal_dir = data_dir / "images", data_dir / "labels", data_dir / "zonal_labels"
    marksheet = data_dir / "clinical_information" / "marksheet.csv"
    clinical = _clinical(marksheet if marksheet.is_file() else None)
    centers = (
        pd.read_csv(marksheet, dtype=str)
        .assign(accession_id=lambda s: s["patient_id"].str.strip() + "_" + s["study_id"].str.strip())
        .drop_duplicates("accession_id")
        .set_index("accession_id")["center"]
        if site_tree is not None and marksheet.is_file()
        else None
    )
    if site_tree is not None:
        shutil.rmtree(site_tree, ignore_errors=True)

    rows: list[dict[str, object]] = []
    for accession in tqdm(cases, desc="cases"):
        images = write_case(accession, images_dir, labels_dir, zonal_dir, out_dir / "images")
        patient_id, study_id = accession.split("_", 1)
        clinical_row = clinical.loc[accession] if accession in clinical.index else None
        for modality in MODALITIES:
            rows.append(
                {
                    "accession_id": accession,
                    "image_series_uid": series_uid(patient_id, study_id, modality),
                    "Image date": clinical_row["Image date"] if clinical_row is not None else "",
                    "Modality": MODALITY_CONCEPT_NAME,
                    "Anatomy": ANATOMY_CONCEPT_NAME,
                    "ISUP grade group": clinical_row["ISUP grade group"] if clinical_row is not None else None,
                    "Clinically significant cancer": clinical_row["Clinically significant cancer"]
                    if clinical_row is not None
                    else "",
                    "PI-RADS": clinical_row["PI-RADS"] if clinical_row is not None else None,
                }
            )
        if site_tree is not None:
            center = str(centers.get(accession, "UNKNOWN")).strip() if centers is not None else "UNKNOWN"
            _link_site_tree(site_tree, center, accession, images, labels_dir, zonal_dir)

    pd.DataFrame(rows, columns=DATAFRAME_COLUMNS).to_csv(out_dir / "dataframe.csv", index=False)
    print(f"✅ {len(cases)} case(s), {len(rows)} series → {out_dir}/images and {out_dir}/dataframe.csv", flush=True)
    if site_tree is not None:
        print(f"✅ planner site tree → {site_tree}/<CENTER>", flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="the download: images/, labels/, zonal_labels/, clinical_information/",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="where images/ and dataframe.csv go (default: --data-dir)"
    )
    parser.add_argument(
        "--num-cases", type=int, help="the first N accessions (sorted) with every scan and both masks (default: all)"
    )
    parser.add_argument(
        "--site-tree",
        type=Path,
        help="also write a PicaiDataset site tree per centre here, for `make plan` without dcm2niix",
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir or args.data_dir
    cases = select_cases(
        args.data_dir / "images", args.data_dir / "labels", args.data_dir / "zonal_labels", args.num_cases
    )
    if not cases:
        print(
            f"❌ no complete case under {args.data_dir} "
            "(need <p>/<p>_<s>_{t2w,adc,hbv}.mha + labels/ + zonal_labels/)",
            file=sys.stderr,
        )
        return 1
    prepare(args.data_dir, out_dir, cases, args.site_tree)
    return 0


if __name__ == "__main__":
    sys.exit(main())
