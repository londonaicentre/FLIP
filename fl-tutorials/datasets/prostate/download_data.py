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
# It then puts every zonal label on its whole-gland sibling's grid (see
# pad_zonal_labels_to_gland_grid): picai_labels publishes some of the HeviAI23
# zonal labels on a centre crop of the T2W rather than the T2W itself.

import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

import nibabel as nib
import numpy as np
from tqdm import tqdm

ZENODO_FOLD_URL = "https://zenodo.org/records/6624726/files/picai_public_images_fold{fold}.zip?download=1"
LABELS_URL = "https://github.com/DIAGNijmegen/picai_labels/archive/refs/heads/main.zip"
LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/whole_gland/AI/Bosma22b"
ZONAL_LABELS_SUBDIR = "picai_labels-main/anatomical_delineations/zonal_pz_tz/AI/HeviAI23"
CLINICAL_INFO_FILE = "picai_labels-main/clinical_information/marksheet.csv"
# Where the zonal labels are kept exactly as picai_labels publishes them, beside the padded copies.
ZONAL_LABELS_RAW_DIRNAME = "zonal_labels_raw"
_PADDING_SUFFIX = ".padding.nii.gz"
# Header rounding between two files describing one grid (float32 pixdim/qform, a label writer that
# re-derived its direction cosines): the 3x3 blocks agree to this, and voxel centres to this fraction
# of a voxel (0.08 is the worst seen across all 1,500 studies). Both are far inside the 0.5 voxel at
# which a nearest-neighbour assignment would change.
_GRID_ATOL = 1e-3
_LATTICE_ATOL_VOXELS = 0.25


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


def lattice_offset(target: nib.Nifti1Image, source: nib.Nifti1Image) -> tuple[int, int, int]:
    """Where ``source``'s voxel (0, 0, 0) sits in ``target``'s index space.

    Only defined when ``source`` is a sub-lattice of ``target``: the same spacing and direction,
    voxel centres coinciding with ``target``'s (to header rounding), and every ``source`` voxel
    inside ``target``'s box. Anything else raises, because padding it would move or interpolate
    voxels — the caller must not quietly produce a label that no longer matches its model's output.

    Args:
        target: The grid to place ``source`` on.
        source: The sub-grid.

    Returns:
        tuple[int, int, int]: ``target`` voxel index of ``source``'s first voxel.

    Raises:
        ValueError: ``source`` is not a sub-lattice of ``target``.
    """
    t, s = np.asarray(target.affine, dtype=np.float64), np.asarray(source.affine, dtype=np.float64)
    if not np.allclose(t[:3, :3], s[:3, :3], atol=_GRID_ATOL):
        raise ValueError("source and target differ in voxel spacing or direction, not just extent")
    offset = np.linalg.solve(t[:3, :3], s[:3, 3] - t[:3, 3])
    rounded = np.rint(offset)
    if not np.allclose(offset, rounded, atol=_LATTICE_ATOL_VOXELS):
        raise ValueError(f"source voxel centres are not on the target lattice (offset {offset} voxels)")
    start = rounded.astype(int)
    if (start < 0).any() or (start + np.asarray(source.shape[:3]) > np.asarray(target.shape[:3])).any():
        raise ValueError(f"source extends outside the target box (start {start.tolist()}, size {source.shape[:3]})")
    return int(start[0]), int(start[1]), int(start[2])


def pad_to_grid(cropped: nib.Nifti1Image, target: nib.Nifti1Image) -> nib.Nifti1Image:
    """``cropped`` on ``target``'s grid: its voxels where they already are, zeros everywhere else.

    Exact by construction — no voxel moves and none is interpolated (see ``lattice_offset``). The
    result keeps ``cropped``'s own affine, shifted back by the integer offset, rather than adopting
    ``target``'s: the two describe one grid to header rounding, and ``cropped``'s is the one the
    crop was cut from (the T2W's), so the pad does not import the other file's rounding.

    Args:
        cropped: A label on a sub-lattice of ``target``.
        target: The grid to pad onto; only its shape and lattice are used.

    Returns:
        nib.Nifti1Image: ``target``-shaped label, ``cropped``'s dtype and header fields.
    """
    start = lattice_offset(target, cropped)
    data = np.asanyarray(cropped.dataobj)
    padded = np.zeros(target.shape[:3], dtype=data.dtype)
    padded[tuple(slice(s, s + n) for s, n in zip(start, cropped.shape[:3]))] = data
    affine = np.asarray(cropped.affine, dtype=np.float64).copy()
    affine[:3, 3] -= affine[:3, :3] @ np.asarray(start, dtype=np.float64)
    out = nib.Nifti1Image(padded, affine, header=cropped.header)
    out.set_data_dtype(data.dtype)
    return out


def pad_zonal_labels_to_gland_grid(labels_dir: Path, zonal_labels_dir: Path, raw_dir: Path) -> tuple[int, int]:
    """Put every zonal label on its whole-gland sibling's grid, keeping the published file beside it.

    picai_labels' HeviAI23 zonal labels are the model's argmax exported on PI-CAI's preprocessing
    grid — picai_prep's ``crop_or_pad(physical_size=[81, 192, 192], crop_only=True)``, a centre crop
    of the T2W at native spacing. Scans that already fit in 192 × 192 × 81 mm come out unchanged
    (233 of the 300 fold-0 studies); larger acquisitions, e.g. PCNN's 350 mm field of view, lose
    their outer rows, columns or slices (the other 67). The whole-gland (Bosma22b) label is on the
    full T2W grid for every study, so it is the target here. Padding is exact: the crop sits on the
    T2W lattice, and the model's own softmax (Zenodo 7615350) is zero outside the box, so nothing
    is invented or lost. With this done once at download, the image, the whole-gland mask and the
    zonal mask share one set of dimensions on disk, in XNAT after enrichment, and in any viewer.

    Idempotent: a label already on its sibling's grid is left alone, so re-running is cheap and a
    previously padded tree is a no-op. The published file is copied to ``raw_dir`` before its
    padded replacement is written, and the replacement lands via an atomic rename, so an
    interrupted run leaves either the original or the padded file, never a half-written one.

    Args:
        labels_dir: The whole-gland labels (``<patient>_<study>.nii.gz``), one per zonal label.
        zonal_labels_dir: The zonal labels; padded in place.
        raw_dir: Where the published zonal files are kept, same names.

    Returns:
        tuple[int, int]: ``(padded on this run, already on the grid)``.

    Raises:
        FileNotFoundError: A zonal label has no whole-gland sibling.
        ValueError: A zonal label is not a sub-lattice of its sibling (see ``lattice_offset``).
    """
    for leftover in zonal_labels_dir.glob(f"*{_PADDING_SUFFIX}"):
        leftover.unlink()
    padded = on_grid = 0
    for zonal_path in sorted(zonal_labels_dir.glob("*.nii.gz")):
        gland_path = labels_dir / zonal_path.name
        if not gland_path.exists():
            raise FileNotFoundError(f"{zonal_path.name}: no whole-gland label in {labels_dir} to take the grid from")
        zonal, gland = nib.load(zonal_path), nib.load(gland_path)
        try:
            start = lattice_offset(gland, zonal)
        except ValueError as exc:
            raise ValueError(f"{zonal_path.name}: {exc}") from exc
        if zonal.shape[:3] == gland.shape[:3] and start == (0, 0, 0):
            on_grid += 1
            continue
        result = pad_to_grid(zonal, gland)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_copy = raw_dir / zonal_path.name
        if not raw_copy.exists():
            shutil.copy2(zonal_path, raw_copy)
        staging = zonal_path.with_name(zonal_path.name.removesuffix(".nii.gz") + _PADDING_SUFFIX)
        nib.save(result, staging)
        os.replace(staging, zonal_path)
        padded += 1
    return padded, on_grid


if __name__ == "__main__":
    default_data_dir = Path(__file__).parent.parent.parent / "data" / "prostate"
    data_dir = Path(os.environ.get("DATA_DIR", default_data_dir))
    folds = os.environ.get("FOLDS", "0 1 2 3 4").split()

    download_images(data_dir, folds)
    download_labels(data_dir)
    n_padded, n_on_grid = pad_zonal_labels_to_gland_grid(
        data_dir / "labels", data_dir / "zonal_labels", data_dir / ZONAL_LABELS_RAW_DIRNAME
    )
    print(
        f"Zonal labels on the whole-gland grid: {n_padded} padded now, {n_on_grid} already there"
        + (f" (published files kept in {data_dir / ZONAL_LABELS_RAW_DIRNAME})" if n_padded else "")
    )
    print(f"Done. Images: {data_dir / 'images'}  Labels: {data_dir / 'labels'}")
