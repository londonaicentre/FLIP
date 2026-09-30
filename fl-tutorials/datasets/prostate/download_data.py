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
# Downloads the PI-CAI dataset (https://zenodo.org/records/6624726) and its
# whole-gland + zonal (PZ/TZ) prostate segmentation labels
# (https://github.com/DIAGNijmegen/picai_labels). Each fold zip is ~5GB; FOLDS
# defaults to all 5 folds. Already-downloaded folds/labels (marked by a .done
# marker written after a successful extract) are skipped on re-run.

import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

from tqdm import tqdm

ZENODO_FOLD_URL = "https://zenodo.org/records/6624726/files/picai_public_images_fold{fold}.zip?download=1"
LABELS_URL = "https://github.com/DIAGNijmegen/picai_labels/archive/refs/heads/main.zip"
LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/whole_gland/AI/Guerbet23"
ZONAL_LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/zonal_pz_tz/AI/Yuan23"
CLINICAL_INFO_FILE = "picai_labels-main/clinical_information/marksheet.csv"


def download(url: str, dest: Path) -> None:
    with tqdm(unit="B", unit_scale=True, unit_divisor=1024, desc=dest.name) as bar:

        def report(block_num: int, block_size: int, total_size: int) -> None:
            if bar.total is None and total_size > 0:
                bar.total = total_size
            bar.update(block_size)

        urllib.request.urlretrieve(url, dest, reporthook=report)


def extract(zip_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        for member in tqdm(members, desc=f"Unzipping {zip_path.name}", unit="file"):
            zf.extract(member, dest_dir)


def download_images(data_dir: Path, folds: list[str]) -> None:
    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for fold in folds:
        marker = images_dir / f".fold{fold}.done"
        if marker.exists():
            print(f"Fold {fold} already downloaded, skipping.")
            continue
        zip_path = data_dir / f"picai_public_images_fold{fold}.zip"
        download(ZENODO_FOLD_URL.format(fold=fold), zip_path)
        extract(zip_path, images_dir)
        zip_path.unlink()
        marker.touch()


def download_labels(data_dir: Path) -> None:
    labels_dir = data_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    labels_marker = labels_dir / ".labels.done"

    zonal_labels_dir = data_dir / "zonal_labels"
    zonal_labels_dir.mkdir(parents=True, exist_ok=True)
    zonal_labels_marker = zonal_labels_dir / ".zonal_labels.done"

    clinical_dir = data_dir / "clinical_information"
    clinical_dir.mkdir(parents=True, exist_ok=True)
    clinical_marker = clinical_dir / ".clinical.done"

    if labels_marker.exists() and zonal_labels_marker.exists() and clinical_marker.exists():
        print("Labels, zonal labels, and clinical information already downloaded, skipping.")
        return

    # Same archive backs the whole-gland labels, the zonal (PZ/TZ) labels, and the
    # clinical marksheet (patient/study -> center, PSA, PI-RADS, ISUP, csPCa), so one
    # download covers all three.
    zip_path = data_dir / "picai_labels.zip"
    tmp_dir = data_dir / "picai_labels_tmp"

    download(LABELS_URL, zip_path)
    extract(zip_path, tmp_dir)

    if not labels_marker.exists():
        for item in (tmp_dir / LABELS_SUBDIR).iterdir():
            shutil.move(str(item), str(labels_dir / item.name))
        labels_marker.touch()

    if not zonal_labels_marker.exists():
        for item in (tmp_dir / ZONAL_LABELS_SUBDIR).iterdir():
            shutil.move(str(item), str(zonal_labels_dir / item.name))
        zonal_labels_marker.touch()

    if not clinical_marker.exists():
        shutil.copy(tmp_dir / CLINICAL_INFO_FILE, clinical_dir / "marksheet.csv")
        clinical_marker.touch()

    zip_path.unlink()
    shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    data_dir = Path(os.environ.get("DATA_DIR", default_data_dir))
    folds = os.environ.get("FOLDS", "0 1 2 3 4").split()

    download_images(data_dir, folds)
    download_labels(data_dir)
    print(f"Done. Images: {data_dir / 'images'}  Labels: {data_dir / 'labels'}")
