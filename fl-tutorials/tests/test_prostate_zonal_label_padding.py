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

"""``download_data.py`` puts every HeviAI23 zonal label on its whole-gland sibling's grid.

picai_labels publishes 67 of the 300 fold-0 zonal labels on PI-CAI's centre-crop preprocessing grid
(picai_prep ``crop_or_pad(physical_size=[81, 192, 192], crop_only=True)``) instead of the T2W. The
crop is a sub-lattice of the T2W — same spacing, same direction, voxel centres coinciding — so the
inverse is an exact zero pad. These tests pin that: nothing moves, nothing is interpolated, the
published file is kept, a label already on the grid is untouched, re-running is a no-op, and the
tutorial loader sees the same tensor from the padded file as from the published one. Anything that
is *not* a sub-lattice must be refused rather than resampled.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import nibabel as nib
import numpy as np
import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT

DOWNLOAD_PY = TUTORIALS_ROOT / "datasets" / "prostate" / "download_data.py"
DATASET_PY = TUTORIALS_ROOT / "flower" / "3d_prostate_segmentation" / "dataset.py"

ACCESSION = "10000_1000000"
ON_GRID_ACCESSION = "10003_1000003"
GLAND_SHAPE = (16, 14, 9)
CROP_START = (3, 2, 1)
CROP_SHAPE = (9, 8, 6)
SPACING = (0.5, 0.5, 3.0)


def load_module(name: str, path: Path) -> ModuleType:
    """A loose tutorial script loaded from its path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def download_module() -> ModuleType:
    return load_module("prostate_download_data", DOWNLOAD_PY)


@pytest.fixture(scope="module")
def dataset_module() -> ModuleType:
    return load_module("prostate_tutorial_dataset", DATASET_PY)


def gland_affine() -> np.ndarray:
    """An LPS-stored T2W affine with the tilt PI-CAI's axial scans carry (direction not identity)."""
    tilt = np.deg2rad(14.0)
    rotation = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
    affine = np.eye(4)
    affine[:3, :3] = rotation @ np.diag([-SPACING[0], -SPACING[1], SPACING[2]])
    affine[:3, 3] = (109.2, 76.6, -84.3)
    return affine


def crop_affine(gland: np.ndarray, start: tuple[float, float, float]) -> np.ndarray:
    """The affine of a sub-box whose first voxel is ``start`` (in gland voxels) — what picai_prep writes."""
    affine = gland.copy()
    affine[:3, 3] = gland[:3, :3] @ np.asarray(start, dtype=float) + gland[:3, 3]
    return affine


def zonal_crop_data() -> np.ndarray:
    """PZ (1) and TZ (2) blocks inside the crop, off-centre so a flip or shift would show."""
    data = np.zeros(CROP_SHAPE, dtype=np.uint8)
    data[1:5, 1:6, 1:4] = 1
    data[5:8, 2:7, 2:5] = 2
    return data


def write_tree(root: Path, cropped_start: tuple[float, float, float] = CROP_START) -> dict[str, Path]:
    """``labels/`` + ``zonal_labels/`` with one cropped study and one already on the grid."""
    labels, zonal = root / "labels", root / "zonal_labels"
    labels.mkdir()
    zonal.mkdir()
    gland = np.zeros(GLAND_SHAPE, dtype=np.uint8)
    gland[2:13, 1:11, 1:8] = 1
    for accession in (ACCESSION, ON_GRID_ACCESSION):
        nib.save(nib.Nifti1Image(gland, gland_affine()), labels / f"{accession}.nii.gz")
    nib.save(
        nib.Nifti1Image(zonal_crop_data(), crop_affine(gland_affine(), cropped_start)),
        zonal / f"{ACCESSION}.nii.gz",
    )
    on_grid = np.zeros(GLAND_SHAPE, dtype=np.uint8)
    on_grid[4:9, 3:8, 2:6] = 1
    on_grid[9:12, 3:8, 2:6] = 2
    nib.save(nib.Nifti1Image(on_grid, gland_affine()), zonal / f"{ON_GRID_ACCESSION}.nii.gz")
    return {"labels": labels, "zonal": zonal, "raw": root / "zonal_labels_raw"}


def run(download_module: ModuleType, tree: dict[str, Path]) -> tuple[int, int]:
    return download_module.pad_zonal_labels_to_gland_grid(tree["labels"], tree["zonal"], tree["raw"])


def test_cropped_label_is_padded_onto_the_gland_grid_without_moving_a_voxel(download_module, tmp_path):
    tree = write_tree(tmp_path)
    published_bytes = (tree["zonal"] / f"{ACCESSION}.nii.gz").read_bytes()

    assert run(download_module, tree) == (1, 1)

    padded = nib.load(tree["zonal"] / f"{ACCESSION}.nii.gz")
    gland = nib.load(tree["labels"] / f"{ACCESSION}.nii.gz")
    assert padded.shape == gland.shape
    assert np.allclose(padded.affine, gland.affine, atol=1e-5)
    assert padded.get_data_dtype() == np.uint8

    array = np.asanyarray(padded.dataobj)
    window = tuple(slice(s, s + n) for s, n in zip(CROP_START, CROP_SHAPE))
    assert np.array_equal(array[window], zonal_crop_data())
    outside = array.copy()
    outside[window] = 0
    assert not outside.any(), "padding must be zero everywhere outside the published crop"

    # The physical position of every labelled voxel is unchanged: index i in the padded file and
    # index i - start in the published file map to the same point in space.
    published = nib.load(tree["raw"] / f"{ACCESSION}.nii.gz")
    labelled = np.argwhere(array > 0)
    here = nib.affines.apply_affine(padded.affine, labelled)
    there = nib.affines.apply_affine(published.affine, labelled - np.asarray(CROP_START))
    assert np.allclose(here, there, atol=1e-6)

    assert (tree["raw"] / f"{ACCESSION}.nii.gz").read_bytes() == published_bytes


def test_label_already_on_the_grid_is_left_untouched(download_module, tmp_path):
    tree = write_tree(tmp_path)
    on_grid = tree["zonal"] / f"{ON_GRID_ACCESSION}.nii.gz"
    before = on_grid.read_bytes()

    run(download_module, tree)

    assert on_grid.read_bytes() == before
    assert not (tree["raw"] / f"{ON_GRID_ACCESSION}.nii.gz").exists()


def test_rerun_is_a_no_op(download_module, tmp_path):
    tree = write_tree(tmp_path)
    run(download_module, tree)
    snapshot = {p.name: p.read_bytes() for p in tree["zonal"].glob("*.nii.gz")}

    assert run(download_module, tree) == (0, 2)
    assert {p.name: p.read_bytes() for p in tree["zonal"].glob("*.nii.gz")} == snapshot
    assert sorted(p.name for p in tree["raw"].iterdir()) == [f"{ACCESSION}.nii.gz"]


def test_interrupted_run_leaves_no_staging_file_behind(download_module, tmp_path):
    tree = write_tree(tmp_path)
    leftover = tree["zonal"] / f"{ACCESSION}{download_module._PADDING_SUFFIX}"
    leftover.write_bytes(b"half-written")

    run(download_module, tree)

    assert not leftover.exists()
    assert sorted(p.name for p in tree["zonal"].glob("*.nii.gz")) == [
        f"{ACCESSION}.nii.gz",
        f"{ON_GRID_ACCESSION}.nii.gz",
    ]


def test_tutorial_loader_sees_the_same_tensor_from_padded_and_published(download_module, dataset_module, tmp_path):
    """The pad is exactly what ``build_loader`` already did on the fly, so training does not change."""
    tree = write_tree(tmp_path)
    run(download_module, tree)
    gland = tree["labels"] / f"{ACCESSION}.nii.gz"
    loader = dataset_module.build_loader()

    def zonal_tensor(zonal_path: Path) -> torch.Tensor:
        loaded = loader(
            {
                dataset_module.IMAGE_KEY: gland,
                dataset_module.WHOLE_GLAND_KEY: gland,
                dataset_module.PZ_TZ_KEY: zonal_path,
            }
        )
        return loaded[dataset_module.PZ_TZ_KEY].as_tensor()

    from_published = zonal_tensor(tree["raw"] / f"{ACCESSION}.nii.gz")
    from_padded = zonal_tensor(tree["zonal"] / f"{ACCESSION}.nii.gz")
    assert from_padded.shape == (1, *GLAND_SHAPE)
    assert torch.equal(from_padded, from_published)
    assert int((from_padded > 0).sum()) == int((zonal_crop_data() > 0).sum())


@pytest.mark.parametrize(
    ("start", "reason"),
    [
        ((3.5, 2, 1), "half a voxel off the lattice"),
        ((-1, 2, 1), "starts before the gland box"),
        ((9, 2, 1), "runs past the gland box"),
    ],
)
def test_a_crop_that_is_not_a_sub_lattice_is_refused(download_module, tmp_path, start, reason):
    tree = write_tree(tmp_path, cropped_start=start)
    published = (tree["zonal"] / f"{ACCESSION}.nii.gz").read_bytes()

    with pytest.raises(ValueError, match=f"{ACCESSION}.*(lattice|outside)"):
        run(download_module, tree)

    assert (tree["zonal"] / f"{ACCESSION}.nii.gz").read_bytes() == published, reason
    assert not tree["raw"].exists()


def test_header_rounding_in_the_sibling_is_tolerated_and_kept_out_of_the_result(download_module, tmp_path):
    """The whole-gland writer rounded its header: still one lattice, and the pad keeps the zonal file's affine."""
    tree = write_tree(tmp_path)
    gland_path = tree["labels"] / f"{ACCESSION}.nii.gz"
    noisy = gland_affine()
    noisy[:3, :3] += 2e-4  # direction cosines re-derived by another writer
    noisy[:3, 3] += 0.02  # 0.04 voxel in-plane
    nib.save(nib.Nifti1Image(np.asanyarray(nib.load(gland_path).dataobj), noisy), gland_path)

    assert run(download_module, tree) == (1, 1)

    padded = nib.load(tree["zonal"] / f"{ACCESSION}.nii.gz")
    assert np.allclose(padded.affine, gland_affine(), atol=1e-6), "the crop's own (T2W) affine, shifted back"
    assert not np.allclose(padded.affine, noisy, atol=1e-6)


def test_a_different_spacing_is_refused(download_module, tmp_path):
    tree = write_tree(tmp_path)
    affine = crop_affine(gland_affine(), CROP_START)
    affine[:3, :3] *= 2.0
    nib.save(nib.Nifti1Image(zonal_crop_data(), affine), tree["zonal"] / f"{ACCESSION}.nii.gz")

    with pytest.raises(ValueError, match="spacing or direction"):
        run(download_module, tree)


def test_a_zonal_label_without_a_whole_gland_sibling_is_an_error(download_module, tmp_path):
    tree = write_tree(tmp_path)
    (tree["labels"] / f"{ACCESSION}.nii.gz").unlink()

    with pytest.raises(FileNotFoundError, match=ACCESSION):
        run(download_module, tree)
