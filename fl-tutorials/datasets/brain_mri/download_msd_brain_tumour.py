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

"""Download MSD Task01_BrainTumour and extract the training half of it.

The Medical Segmentation Decathlon publishes Task01 as one 7.6 GB tar (BraTS 2016/17: 484 training
volumes with labels, 266 unlabelled test volumes; CC BY-SA 4.0). Only ``imagesTr``, ``labelsTr`` and
``dataset.json`` are extracted — the test images have no labels and nothing here uses them — and the
AppleDouble ``._*`` members the archive carries are dropped, the trap ``download_spleen_dataset.py``
documents. The tar is checked against MSD's published md5 before anything is extracted and kept
beside the extract, so a re-run verifies and skips rather than fetching 7.6 GB again.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tarfile
from pathlib import Path

import requests
from tqdm import tqdm

MSD_URL = "https://msd-for-monai.s3-us-west-2.amazonaws.com/Task01_BrainTumour.tar"
# MONAI's DecathlonDataset pins the same value for this archive.
MSD_MD5 = "240a19d752f0d9e9101544901065d872"  # pragma: allowlist secret
DATASET_DIR_NAME = "Task01_BrainTumour"
KEEP_DIRS = ("imagesTr", "labelsTr")
KEEP_FILES = ("dataset.json",)
CHUNK_BYTES = 8 * 1024 * 1024


def wanted(member_name: str) -> bool:
    """Whether an archive member is part of the training extract."""
    parts = [p for p in member_name.split("/") if p not in ("", ".")]
    if parts and parts[0] == DATASET_DIR_NAME:
        parts = parts[1:]
    if not parts or any(p.startswith("._") for p in parts):
        return False
    return parts[0] in KEEP_DIRS or (len(parts) == 1 and parts[0] in KEEP_FILES)


def md5sum(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - the checksum MSD publishes for the archive
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_md5(path: Path, expected: str) -> None:
    """Raises SystemExit naming both digests when ``path`` is not the archive MSD published."""
    actual = md5sum(path)
    if actual != expected:
        raise SystemExit(f"❌ {path}: md5 {actual} does not match the expected {expected} — delete it and re-run")


def download(url: str, dest: Path, expected_md5: str | None) -> Path:
    """Stream ``url`` to ``dest`` via a ``.part`` sibling, unless ``dest`` is already the verified archive."""
    dest = Path(dest)
    if dest.is_file():
        if expected_md5 is None or md5sum(dest) == expected_md5:
            print(f"✅ {dest} already present" + ("" if expected_md5 is None else " and verified"), flush=True)
            return dest
        print(f"⚠️  {dest} does not match the expected md5 — downloading again", flush=True)
        dest.unlink()
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0)) or None
        with part.open("wb") as handle, tqdm(total=total, unit="B", unit_scale=True, desc=dest.name) as bar:
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                handle.write(chunk)
                bar.update(len(chunk))
    os.replace(part, dest)
    if expected_md5 is not None:
        verify_md5(dest, expected_md5)
    return dest


def extract(tar_path: Path, out_root: Path) -> Path:
    """Extract the wanted members into ``out_root``; returns ``out_root/Task01_BrainTumour``.

    Idempotent: an extract that already has ``dataset.json`` and training images is left alone.
    """
    dataset_dir = Path(out_root) / DATASET_DIR_NAME
    if (dataset_dir / KEEP_FILES[0]).is_file() and any((dataset_dir / KEEP_DIRS[0]).glob("*.nii.gz")):
        print(f"✅ {dataset_dir} already extracted", flush=True)
        return dataset_dir
    print(f"📦 extracting the training half of {tar_path} into {out_root} …", flush=True)
    with tarfile.open(tar_path) as tar:
        members = [m for m in tar.getmembers() if wanted(m.name)]
        tar.extractall(out_root, members=members, filter="data")
    print(f"✅ {dataset_dir}: {len(list((dataset_dir / 'imagesTr').glob('*.nii.gz')))} training volumes", flush=True)
    return dataset_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=MSD_URL)
    parser.add_argument(
        "--tar", type=Path, default=Path("data") / f"{DATASET_DIR_NAME}.tar", help="where the archive lands"
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("data"), help="the extract's parent: <out-dir>/Task01_BrainTumour/"
    )
    parser.add_argument("--md5", default=MSD_MD5, help="expected archive md5; --md5 '' skips the check")
    args = parser.parse_args(argv)

    archive = download(args.url, args.tar, args.md5 or None)
    extract(archive, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
