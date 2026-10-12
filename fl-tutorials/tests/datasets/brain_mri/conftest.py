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

"""Fixtures shared by the brain_mri dataset-tooling tests: a miniature MSD Task01 extract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

DATASETS_DIR = Path(__file__).resolve().parents[3] / "datasets"
sys.path.insert(0, str(DATASETS_DIR))

# The channel order and names exactly as MSD's dataset.json declares them.
MODALITIES = {"0": "FLAIR", "1": "T1w", "2": "t1gd", "3": "T2w"}
SHAPE = (6, 5, 3)  # (i, j, k); the real volumes are 240x240x155


def make_case(images_dir: Path, labels_dir: Path, case: str, seed: int) -> None:
    """One 4-D image (four channels, distinct non-negative values) and its 3-D label."""
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 3000, size=(*SHAPE, len(MODALITIES))).astype(np.float32)
    label = rng.integers(0, 4, size=SHAPE).astype(np.uint8)
    affine = np.diag([-1.0, -1.0, 1.0, 1.0])
    affine[:3, 3] = [120.0, 100.0, -70.0]
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(image, affine), str(images_dir / f"{case}.nii.gz"))
    nib.save(nib.Nifti1Image(label, affine), str(labels_dir / f"{case}.nii.gz"))


@pytest.fixture
def msd_extract(tmp_path: Path) -> Path:
    """``data/Task01_BrainTumour/{dataset.json, imagesTr, labelsTr}`` with three cases."""
    root = tmp_path / "data" / "Task01_BrainTumour"
    root.mkdir(parents=True)
    (root / "dataset.json").write_text(json.dumps({"name": "BRATS", "modality": MODALITIES, "licence": "CC-BY-SA 4.0"}))
    for index, case in enumerate(("BRATS_001", "BRATS_002", "BRATS_003"), start=1):
        make_case(root / "imagesTr", root / "labelsTr", case, seed=index)
    (root / "imagesTr" / "._BRATS_001.nii.gz").write_bytes(b"\x00\x05\x16\x07")
    return root
