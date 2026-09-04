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
# Partitions the converted NIfTI scans + whole-gland + zonal (PZ/TZ) labels
# into one folder per acquiring center (RUMC, PCNN, ZGT), using the "center"
# column of data/clinical_information/marksheet.csv. Each site folder is
# symlinked back to the shared data/nifti, data/labels, and data/zonal_labels
# files (no duplication) plus a manifest.csv listing that site's
# (patient_id, study_id) rows, ready to feed PicaiSegDataset per simulated FL
# client.

import argparse
import csv
from pathlib import Path


def load_marksheet(clinical_dir: Path) -> list[dict[str, str]]:
    marksheet_path = clinical_dir / "marksheet.csv"
    if not marksheet_path.exists():
        raise FileNotFoundError(f"{marksheet_path} not found. Run download_data.py first.")
    with open(marksheet_path, newline="") as f:
        return list(csv.DictReader(f))


def link_to(link: Path, target: Path) -> None:
    """Point `link` at `target`, replacing a stale symlink if one is already there.

    Links are written as resolved absolute paths, so moving the shared `nifti/`,
    `labels/` or `zonal_labels/` trees leaves every existing link dangling. A
    dangling link is invisible to `Path.exists()` (which follows it), so without
    the `is_symlink()` check re-running would raise `FileExistsError` on the
    first stale entry instead of repairing it.

    Args:
        link: Path of the symlink to create inside a site folder.
        target: File the link should point at.
    """
    if link.is_symlink() and not link.exists():
        link.unlink()
    if not link.exists():
        link.symlink_to(target.resolve())


def partition(data_dir: Path) -> None:
    nifti_dir = data_dir / "nifti"
    labels_dir = data_dir / "labels"
    zonal_labels_dir = data_dir / "zonal_labels"
    sites_dir = data_dir / "sites"

    rows = load_marksheet(data_dir / "clinical_information")

    by_center: dict[str, list[dict[str, str]]] = {}
    skipped = 0
    for row in rows:
        patient_id, study_id, center = row["patient_id"], row["study_id"], row["center"]
        label_path = labels_dir / f"{patient_id}_{study_id}.nii.gz"
        zonal_label_path = zonal_labels_dir / f"{patient_id}_{study_id}.nii.gz"
        scans = sorted((nifti_dir / patient_id).glob(f"{patient_id}_{study_id}_*.nii.gz")) if (
            nifti_dir / patient_id
        ).is_dir() else []
        if not scans or not label_path.exists() or not zonal_label_path.exists():
            skipped += 1
            continue
        by_center.setdefault(center, []).append(row)

    for center, center_rows in by_center.items():
        site_nifti_dir = sites_dir / center / "nifti"
        site_labels_dir = sites_dir / center / "labels"
        site_zonal_labels_dir = sites_dir / center / "zonal_labels"
        site_nifti_dir.mkdir(parents=True, exist_ok=True)
        site_labels_dir.mkdir(parents=True, exist_ok=True)
        site_zonal_labels_dir.mkdir(parents=True, exist_ok=True)

        for row in center_rows:
            patient_id, study_id = row["patient_id"], row["study_id"]
            for scan_path in sorted((nifti_dir / patient_id).glob(f"{patient_id}_{study_id}_*.nii.gz")):
                link_to(site_nifti_dir / scan_path.name, scan_path)

            label_path = labels_dir / f"{patient_id}_{study_id}.nii.gz"
            link_to(site_labels_dir / label_path.name, label_path)

            zonal_label_path = zonal_labels_dir / f"{patient_id}_{study_id}.nii.gz"
            link_to(site_zonal_labels_dir / zonal_label_path.name, zonal_label_path)

        manifest_path = sites_dir / center / "manifest.csv"
        with open(manifest_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["patient_id", "study_id"])
            writer.writeheader()
            for row in center_rows:
                writer.writerow({"patient_id": row["patient_id"], "study_id": row["study_id"]})

        print(f"{center}: {len(center_rows)} studies -> {sites_dir / center}")

    if skipped:
        print(f"Skipped {skipped} marksheet studies with no local scans/labels (partial download).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Partition PI-CAI NIfTI scans + labels by acquiring center")
    # fl-tutorials/data/prostate — see download_data.py's default_data_dir comment.
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent.parent.parent / "data" / "prostate")
    args = parser.parse_args()
    partition(args.data_dir)
