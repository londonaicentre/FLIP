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

"""Pin the shipped 3-D NIfTI chains' RAS orientation, including image/label alignment.

Equivalent voxel data stored under RAS and LPS affines must agree after preprocessing.
Also compare the orientation prefix to a known RAS volume: agreement alone would let
both inputs be transformed to the same wrong orientation. Training stops before its
first random crop/augmentation; training behaviour and resampling fidelity are out of scope.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

import monai.transforms as mt
import nibabel as nib
import numpy as np
import pytest
from monai.transforms.traits import RandomizableTrait
from nibabel.orientations import axcodes2ornt, ornt_transform
from tutorial_apps import DICOM_APPS, TUTORIALS_ROOT, load_module

_SHAPE = (11, 15, 9)
_AFFINE = np.diag([1.5, 1.5, 2.0, 1.0])  # Match the apps' spacing; avoid interpolation changes.


@dataclass(frozen=True)
class NiftiApp:
    app_id: str
    module_path: str
    factories: tuple[str, ...] = ("get_train_transforms", "get_val_transforms")
    keys: tuple[str, ...] = ("image", "label")

    @property
    def path(self) -> Path:
        return TUTORIALS_ROOT / self.module_path

    def transforms(self, factory_name: str) -> mt.Compose:
        factory = getattr(load_module(f"nifti_{self.app_id}", self.path), factory_name)
        kwargs = {"spatial_shape": _SHAPE} if "spatial_shape" in inspect.signature(factory).parameters else {}
        return factory(**kwargs)


NIFTI_APPS = (
    NiftiApp("nvflare_spleen", "nvflare/image_segmentation/3d_spleen_segmentation/app_files/transforms.py"),
    NiftiApp(
        "nvflare_spleen_eval",
        "nvflare/image_evaluation/3d_spleen_segmentation_evaluation/app_files/transforms.py",
        factories=("get_eval_transforms",),
    ),
    NiftiApp("flower_spleen", "flower/3d_spleen_segmentation/app/transforms.py"),
    NiftiApp("flower_spleen_eval", "flower/3d_spleen_segmentation_evaluation/app/transforms.py"),
    NiftiApp(
        "nvflare_ldm",
        "nvflare/image_synthesis/latent_diffusion_model/app_files/transforms.py",
        keys=("image",),
    ),
)
_CHAINS = [pytest.param(app, factory, id=f"{app.app_id}/{factory}") for app in NIFTI_APPS for factory in app.factories]


@pytest.fixture(scope="session")
def nifti_phantom(tmp_path_factory):
    """Write tiny image/label pairs with identical physical content in two encodings."""
    image = np.full(_SHAPE, -100, dtype=np.float32)
    label = np.zeros(_SHAPE, dtype=np.uint8)
    # The asymmetric foreground survives CT windowing and has a one-voxel crop border.
    image[1:-1, 1:-1, 1:-1] = 20
    image[2:5, 3:6, 2:4] = 180
    label[2:5, 3:6, 2:4] = 1
    image[6:9, 9:13, 4:7] = 100
    label[6:9, 9:13, 4:7] = 1
    image[3, 11, 6] = 240

    reference = {"image": image, "label": label}
    directory = tmp_path_factory.mktemp("phantom-nifti")
    paths = {"RAS": {}, "LPS": {}}
    to_lps = ornt_transform(axcodes2ornt("RAS"), axcodes2ornt("LPS"))
    for key, data in reference.items():
        ras = nib.Nifti1Image(data, _AFFINE)
        ras.header.set_xyzt_units("mm")
        # Flip voxels AND update the affine translation, preserving world coordinates.
        for codes, volume in (("RAS", ras), ("LPS", ras.as_reoriented(to_lps))):
            path = directory / f"{key}_{codes.lower()}.nii.gz"
            nib.save(volume, path)
            paths[codes][key] = str(path)
    return reference, paths


def _orientation_prefix(chain: mt.Compose) -> mt.Compose:
    indices = [i for i, transform in enumerate(chain.transforms) if isinstance(transform, mt.Orientationd)]
    assert len(indices) == 1, "expected one Orientationd in the app's NIfTI chain"
    return mt.Compose(chain.transforms[: indices[0] + 1])


def _deterministic_prefix(chain: mt.Compose) -> mt.Compose:
    prefix = chain  # Run complete validation/evaluation preprocessing.
    for i, transform in enumerate(chain.transforms):
        if isinstance(transform, RandomizableTrait):
            prefix = mt.Compose(chain.transforms[:i])
            break
    assert any(isinstance(transform, mt.Spacingd) for transform in prefix.transforms), (
        "the deterministic prefix must still exercise Spacingd before random training steps"
    )
    return prefix


@pytest.mark.parametrize("codes", ["RAS", "LPS"])
def test_nifti_phantom_preserves_physical_content(nifti_phantom, codes: str) -> None:
    reference, paths = nifti_phantom
    assert len(set(_SHAPE)) == 3, "axis permutations must change the phantom's shape"
    for key, data in reference.items():
        for axis in range(3):
            assert not np.array_equal(data, np.flip(data, axis)), f"{key} is symmetric on axis {axis}"
        loaded = nib.load(paths[codes][key])
        assert isinstance(loaded, nib.Nifti1Image)
        assert nib.aff2axcodes(loaded.affine) == tuple(codes)
        canonical = nib.as_closest_canonical(loaded)
        np.testing.assert_array_equal(np.asarray(canonical.dataobj), data)
        np.testing.assert_array_equal(canonical.affine, _AFFINE)


@pytest.mark.parametrize(("app", "factory"), _CHAINS)
@pytest.mark.parametrize("codes", ["RAS", "LPS"])
def test_nifti_orientation_prefix_matches_ras_phantom(nifti_phantom, app: NiftiApp, factory: str, codes: str) -> None:
    """Pin values and affine independently of later scaling, cropping and resampling."""
    reference, paths = nifti_phantom
    prefix = _orientation_prefix(app.transforms(factory))
    output = prefix({key: paths[codes][key] for key in app.keys})
    for key in app.keys:
        np.testing.assert_array_equal(np.asarray(output[key]), reference[key][None], err_msg=f"{codes}/{key}")
        np.testing.assert_array_equal(output[key].affine, _AFFINE, err_msg=f"{codes}/{key} affine")


@pytest.mark.parametrize(("app", "factory"), _CHAINS)
def test_nifti_preprocessing_agrees_across_encodings(nifti_phantom, app: NiftiApp, factory: str) -> None:
    """Exercise the app's Orientationd/Spacingd path, with random training steps excluded."""
    _, paths = nifti_phantom
    chain = _deterministic_prefix(app.transforms(factory))
    output = {codes: chain({key: paths[codes][key] for key in app.keys}) for codes in paths}
    for key in app.keys:
        ras, lps = output["RAS"][key], output["LPS"][key]
        assert nib.aff2axcodes(np.asarray(ras.affine)) == ("R", "A", "S"), f"{key} is not RAS"
        np.testing.assert_allclose(np.asarray(ras), np.asarray(lps), rtol=0, atol=1e-6, err_msg=key)
        np.testing.assert_allclose(ras.affine, lps.affine, rtol=0, atol=1e-6, err_msg=f"{key} affine")
    if "label" in app.keys:
        for result in output.values():
            np.testing.assert_allclose(result["image"].affine, result["label"].affine, rtol=0, atol=1e-6)


def test_registry_covers_every_nifti_factory() -> None:
    """Every non-DICOM LoadImaged factory must be exercised, including new app variants."""
    dicom_paths = {app.path.resolve() for app in DICOM_APPS}
    registered = {(app.path.resolve(), factory) for app in NIFTI_APPS for factory in app.factories}
    discovered = set()
    for path in sorted(TUTORIALS_ROOT.rglob("*.py")):
        relative = path.relative_to(TUTORIALS_ROOT)
        if relative.parts[0] == "tests" or any(part.startswith(".") for part in relative.parts):
            continue
        if path.resolve() in dicom_paths:
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
                if name == "LoadImaged":
                    discovered.add((path.resolve(), node.name))
    assert registered, "the NIfTI registry is empty"
    assert discovered == registered, (
        f"unregistered NIfTI factories: {discovered - registered}; stale entries: {registered - discovered}"
    )
