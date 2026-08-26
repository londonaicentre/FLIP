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

"""Which MSD cases ``--num_cases N`` keeps (FLIP#1060).

The reorganiser touches nothing but the filesystem, so the fixtures here are empty
``spleen_<N>.nii.gz`` files and the suite stays CPU-only -- no dataset download, no MONAI
transform, no GPU. The downloader is a loose script rather than a package (same as the tutorial
apps in ``tutorial_apps.py``), so it is loaded from its path.

MSD Task09 case numbers are sparse and unpadded, which is the whole point: a codepoint sort of
``spleen_<N>.nii.gz`` puts ``spleen_19`` before ``spleen_2`` (``'1' < '2'``) and ``spleen_2``
before ``spleen_20`` (``'.' < '0'``), so ``--num_cases 10`` used to keep {10, 12, 13, 14, 16, 17,
18, 19, 2, 20} rather than the ten lowest-numbered cases.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TUTORIALS_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADER_PATH = (
    TUTORIALS_ROOT / "nvflare/image_segmentation/3d_spleen_segmentation/utils/download_spleen_dataset.py"
)

# The real Task09_Spleen training set: 41 cases, sparsely numbered from 2 to 63.
MSD_CASE_NUMBERS = (
    2, 3, 6, 8, 9, 10, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22, 24, 25, 26, 27,
    28, 29, 31, 32, 33, 38, 40, 41, 44, 45, 46, 47, 49, 52, 53, 56, 59, 60, 61, 62, 63,
)  # fmt: skip


@pytest.fixture(scope="module")
def downloader() -> ModuleType:
    """The downloader script, imported from its path under a unique module name."""
    module_name = "fl_tutorials_under_test.download_spleen_dataset"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, DOWNLOADER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {DOWNLOADER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _write_extracted_dataset(root: Path, case_numbers: tuple[int, ...] = MSD_CASE_NUMBERS) -> Path:
    """Lay out an empty stand-in for the extracted archive and return the directory."""
    base_dir = root / "Task09_Spleen"
    images_dir = base_dir / "imagesTr"
    labels_dir = base_dir / "labelsTr"
    images_dir.mkdir(parents=True)
    labels_dir.mkdir(parents=True)
    for case in case_numbers:
        (images_dir / f"spleen_{case}.nii.gz").touch()
        (labels_dir / f"spleen_{case}.nii.gz").touch()
    return base_dir


def _kept_case_numbers(output_dir: Path) -> list[int]:
    """The cases that survived reorganisation, in numeric order."""
    return sorted(int(path.name.removeprefix("subject_")) for path in output_dir.glob("subject_*"))


@pytest.mark.parametrize(
    ("num_cases", "expected"),
    [
        (1, [2]),
        # The ten lowest-numbered cases. Under the old codepoint sort this was
        # [2, 10, 12, 13, 14, 16, 17, 18, 19, 20] -- 3, 6, 8 and 9 skipped for 17..20.
        (10, [2, 3, 6, 8, 9, 10, 12, 13, 14, 16]),
        (41, list(MSD_CASE_NUMBERS)),
    ],
)
def test_num_cases_keeps_the_lowest_numbered_cases(
    downloader: ModuleType, tmp_path: Path, num_cases: int, expected: list[int]
) -> None:
    """``--num_cases N`` keeps the N numerically-lowest cases, not the N lexicographically first."""
    _write_extracted_dataset(tmp_path)

    downloader.reorganise_spleen_dataset(str(tmp_path), num_cases)

    assert _kept_case_numbers(tmp_path) == expected


def test_kept_cases_carry_both_an_image_and_a_label(downloader: ModuleType, tmp_path: Path) -> None:
    """Each kept subject gets the input/label pair the trainer's zero-pairs guard looks for."""
    _write_extracted_dataset(tmp_path)

    downloader.reorganise_spleen_dataset(str(tmp_path), 3)

    for case in (2, 3, 6):
        scans = tmp_path / f"subject_{case}" / "scans"
        assert (scans / f"input_spleen_{case}.nii.gz").is_file()
        assert (scans / f"label_spleen_{case}.nii.gz").is_file()


def test_macos_resource_forks_are_skipped_without_consuming_a_slot(
    downloader: ModuleType, tmp_path: Path
) -> None:
    """The archive ships ``._spleen_<N>.nii.gz`` siblings; they are not cases.

    They must not reach the sort either: natsort would happily interleave them with the real
    volumes, and one counted as a case would silently cost a real case its slot.
    """
    base_dir = _write_extracted_dataset(tmp_path)
    for case in MSD_CASE_NUMBERS[:5]:
        (base_dir / "imagesTr" / f"._spleen_{case}.nii.gz").touch()

    downloader.reorganise_spleen_dataset(str(tmp_path), 5)

    assert _kept_case_numbers(tmp_path) == [2, 3, 6, 8, 9]
