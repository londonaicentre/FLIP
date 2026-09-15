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

"""Pin that the prostate tutorial pairs image and masks by affine, not by storage order.

On the platform an fl-client gets its images from XNAT's dcm2niix, which stores every volume with
the DICOM row axis reversed (``opts->isFlipY``, on by default and not exposed in the registered
command). The PI-CAI masks it pairs them with come from the enrichment upload in the ``.mha``
convention SimpleITK writes — the same voxels in the other row order. Both files carry a correct
affine, so anything that reads by affine sees one image; anything that reads raw arrays sees the
mask mirrored across the anterior-posterior midline of the image (Dice 0.52 against its true
position on PI-CAI 10000_1000000). The old ``.mha`` → NIfTI converter hid this in the simulator by
writing images in the masks' order, so index pairing was right by coincidence.

The phantom here is the platform case: the image written in dcm2niix's storage order, the masks in
SimpleITK's, with the gland block off-centre along every axis so a flip cannot land on itself. The
assertion is on alignment (the image is bright exactly where the whole-gland mask is set), which is
true in any orientation the loader chooses and false under any storage-order mismatch. The
``.mha``-order image is run through the same assertions so the fix is a reorientation rather than a
counter-flip that would break the simulator's own data.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import nibabel as nib
import numpy as np
import pytest
from nibabel.orientations import apply_orientation, inv_ornt_aff
from tutorial_apps import TUTORIALS_ROOT

DATASET_PY = TUTORIALS_ROOT / "flower" / "3d_prostate_segmentation" / "dataset.py"

PATIENT, STUDY, MODALITY = "10000", "1000000", "t2w"
ACCESSION = f"{PATIENT}_{STUDY}"
SPACING = (0.5, 0.5, 3.0)
# Non-square in-plane, so a transposed read changes shape as well as values.
SHAPE = (24, 32, 6)
# The "gland": off-centre along every axis, so no single-axis flip maps it onto itself.
GLAND = (slice(4, 10), slice(20, 28), slice(1, 4))
GLAND_VALUE = 1000.0
# dcm2niix reverses the row axis (index 1 of the stored array) and corrects the affine to match.
DCM2NIIX_ROW_FLIP = np.array([[0, 1], [1, -1], [2, 1]], dtype=float)


@pytest.fixture(scope="module")
def dataset_module() -> ModuleType:
    """``dataset.py`` loaded from its path — the tutorial is loose scripts, not a package."""
    spec = importlib.util.spec_from_file_location("prostate_tutorial_dataset", DATASET_PY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def simpleitk_affine() -> np.ndarray:
    """The affine SimpleITK writes for an axial ``.mha`` with identity direction: LPS storage order.

    ITK's physical space is LPS and NIfTI's is RAS, so the writer negates the first two axes and
    keeps the voxel array in ITK index order. This is what ``picai_labels`` and the old
    ``convert_mha_to_nifti.py`` output carry.
    """
    affine = np.diag([-SPACING[0], -SPACING[1], SPACING[2], 1.0])
    affine[:3, 3] = (100.0, 80.0, -60.0)
    return affine


def phantom_image() -> np.ndarray:
    """Zero everywhere except the gland block, so alignment is a pure equality test."""
    image = np.zeros(SHAPE, dtype=np.float32)
    image[GLAND] = GLAND_VALUE
    return image


def phantom_masks() -> tuple[np.ndarray, np.ndarray]:
    """Whole-gland mask over the block; PZ/TZ mask splitting it in two along the first axis."""
    whole_gland = np.zeros(SHAPE, dtype=np.uint8)
    whole_gland[GLAND] = 1
    pz_tz = np.zeros(SHAPE, dtype=np.uint8)
    split = (GLAND[0].start + GLAND[0].stop) // 2
    pz_tz[slice(GLAND[0].start, split), GLAND[1], GLAND[2]] = 1
    pz_tz[slice(split, GLAND[0].stop), GLAND[1], GLAND[2]] = 2
    return whole_gland, pz_tz


def as_dcm2niix_stores_it(array: np.ndarray, affine: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Re-store a volume the way dcm2niix does: rows reversed, affine corrected so nothing moves."""
    flipped = apply_orientation(array, DCM2NIIX_ROW_FLIP)
    return np.ascontiguousarray(flipped), affine @ inv_ornt_aff(DCM2NIIX_ROW_FLIP, array.shape)


def write_site(root: Path, image_storage: str) -> Path:
    """A one-study ``sites/<CENTER>`` folder in the layout ``partition_by_center.py`` produces."""
    site = root / image_storage
    for sub in ("nifti", "labels", "zonal_labels"):
        (site / sub).mkdir(parents=True)
    (site / "manifest.csv").write_text(f"patient_id,study_id\n{PATIENT},{STUDY}\n")

    affine = simpleitk_affine()
    image = phantom_image()
    if image_storage == "dcm2niix":
        image, image_affine = as_dcm2niix_stores_it(image, affine)
    else:
        image_affine = affine
    nib.save(nib.Nifti1Image(image, image_affine), site / "nifti" / f"{ACCESSION}_{MODALITY}.nii.gz")

    whole_gland, pz_tz = phantom_masks()
    nib.save(nib.Nifti1Image(whole_gland, affine), site / "labels" / f"{ACCESSION}.nii.gz")
    nib.save(nib.Nifti1Image(pz_tz, affine), site / "zonal_labels" / f"{ACCESSION}.nii.gz")
    return site


@pytest.fixture(scope="module", params=["dcm2niix", "simpleitk"])
def site_dir(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same study with the image stored as the platform delivers it, and as the old converter did."""
    return write_site(tmp_path_factory.mktemp("prostate-site"), request.param)


def test_storage_orders_really_differ() -> None:
    """Guard on the fixture: the two image files must differ as arrays and agree as volumes."""
    affine = simpleitk_affine()
    image = phantom_image()
    flipped, flipped_affine = as_dcm2niix_stores_it(image, affine)
    assert not np.array_equal(flipped, image)
    original = nib.Nifti1Image(image, affine)
    restored = nib.as_closest_canonical(nib.Nifti1Image(flipped, flipped_affine))
    assert np.array_equal(np.asarray(restored.dataobj), np.asarray(nib.as_closest_canonical(original).dataobj))


def test_mask_is_set_exactly_where_the_image_is_bright(dataset_module: ModuleType, site_dir: Path) -> None:
    sample = dataset_module.PicaiDataset(site_dir, modality=MODALITY)[0]
    image = sample["image"].numpy()[0]
    mask = sample["mask"].numpy()
    assert sample["accession_id"] == ACCESSION
    assert image.shape == mask.shape[1:], "image and mask must share a grid"

    whole_gland = mask[0] == 1
    assert whole_gland.sum() == np.prod([s.stop - s.start for s in GLAND])
    assert image[whole_gland].min() == GLAND_VALUE, "mask covers voxels that are not gland"
    assert image[~whole_gland].max() == 0.0, "gland voxels lie outside the mask"


def test_zonal_channels_tile_the_whole_gland(dataset_module: ModuleType, site_dir: Path) -> None:
    mask = dataset_module.PicaiDataset(site_dir, modality=MODALITY)[0]["mask"].numpy()
    whole_gland, pz, tz = (mask[c] == 1 for c in range(3))
    assert not (pz & tz).any(), "a voxel cannot be both peripheral and transition zone"
    assert np.array_equal(pz | tz, whole_gland), "PZ ∪ TZ must be exactly the whole gland"
    assert pz.sum() == tz.sum(), "the phantom splits the gland in half"


def test_output_is_independent_of_storage_order(dataset_module: ModuleType, tmp_path: Path) -> None:
    """One study, two files: the loader must hand the model the same tensors for both."""
    samples = [
        dataset_module.PicaiDataset(write_site(tmp_path, storage), modality=MODALITY)[0]
        for storage in ("dcm2niix", "simpleitk")
    ]
    assert np.array_equal(samples[0]["image"].numpy(), samples[1]["image"].numpy())
    assert np.array_equal(samples[0]["mask"].numpy(), samples[1]["mask"].numpy())


def test_fingerprint_pair_shares_one_affine_and_channel_first_zooms(dataset_module: ModuleType, site_dir: Path) -> None:
    """The nnU-Net fingerprint reads spacing from the image header; both NIfTIs must describe one grid."""
    pair = dataset_module.PicaiDataset(site_dir, modality=MODALITY, fingerprint=True)[0]
    image, mask = pair["image"], pair["mask"]
    assert image.shape == (1, *SHAPE)
    assert mask.shape == (3, *SHAPE)
    assert np.allclose(image.affine, mask.affine)
    assert image.header.get_zooms() == pytest.approx((1.0, *SPACING))
    gland = np.asarray(mask.dataobj)[0] == 1
    assert np.asarray(image.dataobj)[0][gland].min() == GLAND_VALUE
