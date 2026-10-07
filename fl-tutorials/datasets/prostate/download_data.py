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
#
# The download itself is resumable and bounded: a 5 GB transfer over a link that goes quiet
# must neither restart from zero nor wait forever. urllib.request.urlretrieve did both — no
# timeout, no Range request — and a fold once hung a few KB short of complete on an idle
# socket. The fetch below streams into a `.part` file, resumes it with a Range header, gives up
# on a socket that sends nothing for READ_TIMEOUT seconds, retries with a pause, and refuses
# to call a file done unless its size matches what the server announced.

import os
import shutil
import time
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

ZENODO_FOLD_URL = "https://zenodo.org/records/6624726/files/picai_public_images_fold{fold}.zip?download=1"
LABELS_URL = "https://github.com/DIAGNijmegen/picai_labels/archive/refs/heads/main.zip"
LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/whole_gland/AI/Guerbet23"
ZONAL_LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/zonal_pz_tz/AI/Yuan23"
CLINICAL_INFO_FILE = "picai_labels-main/clinical_information/marksheet.csv"


CONNECT_TIMEOUT = 30
READ_TIMEOUT = 120  # seconds with no bytes before the attempt is abandoned and resumed
ATTEMPTS = 10
RETRY_PAUSE = 15
CHUNK = 1 << 20


class IncompleteDownload(RuntimeError):
    """The transfer ended before the announced size was reached."""


def _announced_total(response: requests.Response, offset: int) -> int | None:
    """The full file size the server announced, or None when it did not (chunked/generated bodies)."""
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[1].strip()
        return int(total) if total.isdigit() else None
    length = response.headers.get("Content-Length")
    return offset + int(length) if length and length.isdigit() else None


def _fetch_once(session: requests.Session, url: str, part: Path, desc: str) -> None:
    """One attempt: resume `part` from its current size, stream the rest, verify the size."""
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with session.get(url, stream=True, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
        if have and response.status_code == 416:
            # Nothing past `have`: the part is complete if the server's total agrees.
            total = _announced_total(response, 0)
            if total is not None and total == have:
                return
            part.unlink()
            raise IncompleteDownload(f"{desc}: server has {total} bytes, {have} on disk — restarting")
        if have and response.status_code == 200:
            # The server ignored the Range header: it is sending the whole file again.
            part.unlink()
            have = 0
        response.raise_for_status()
        total = _announced_total(response, have)
        with (
            open(part, "ab") as handle,
            tqdm(total=total, initial=have, unit="B", unit_scale=True, unit_divisor=1024, desc=desc) as bar,
        ):
            for chunk in response.iter_content(chunk_size=CHUNK):
                handle.write(chunk)
                bar.update(len(chunk))
    size = part.stat().st_size
    if total is not None and size != total:
        raise IncompleteDownload(f"{desc}: {size} of {total} bytes")


def download(url: str, dest: Path) -> None:
    """Fetch `url` to `dest`, resuming a previous partial transfer and retrying a stalled one.

    The bytes land in `<dest>.part` and are renamed into place only once the whole file is
    there, so a `dest` that exists is always complete.
    """
    part = dest.with_name(dest.name + ".part")
    session = requests.Session()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            _fetch_once(session, url, part, dest.name)
            part.replace(dest)
            return
        except (requests.RequestException, IncompleteDownload) as err:
            if attempt == ATTEMPTS:
                raise
            print(f"⚠️  {err} — retrying in {RETRY_PAUSE}s (attempt {attempt}/{ATTEMPTS})", flush=True)
            time.sleep(RETRY_PAUSE)


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
