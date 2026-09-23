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

"""Which MSD cases a run converts: the lowest N for authoring, the published cohort for reproduction.

Two selectors, never both. ``num_cases`` is the spleen rule (FLIP#1060): N means the N lowest case
numbers in natural order, so a larger N is a superset of a smaller one and the published 40-case cohort
is the first 40. ``metadata_table`` is the reproducible path: exactly the ``Subject`` column of a fetched
``source/dicom_metadata.csv``, so regenerating the DICOMs from a later MSD download can only ever
produce the studies the published tables describe.
"""

from __future__ import annotations

import csv
from pathlib import Path

from natsort import natsorted

VOLUME_SUFFIX = ".nii.gz"


def case_id(path: Path) -> str:
    """``BRATS_001`` from ``BRATS_001.nii.gz``."""
    return path.name.removesuffix(VOLUME_SUFFIX)


def list_cases(images_dir: Path) -> list[str]:
    """Every case under ``images_dir`` in natural order, macOS ``._`` resource forks excluded.

    Raises:
        SystemExit: If there are none — a missing download must not look like an empty cohort.
    """
    cases = natsorted(case_id(p) for p in Path(images_dir).glob(f"*{VOLUME_SUFFIX}") if not p.name.startswith("._"))
    if not cases:
        raise SystemExit(
            f"❌ no {VOLUME_SUFFIX} volumes under {images_dir} — "
            "run `make -C fl-tutorials download-brain-mri-msd-raw` first"
        )
    return cases


def select_cases(images_dir: Path, num_cases: int | None = None, metadata_table: Path | None = None) -> list[str]:
    """The cases a run converts, in natural order.

    Args:
        images_dir: The MSD ``imagesTr`` directory.
        num_cases: Keep the N lowest-numbered cases. ``None`` (with no table) keeps them all.
        metadata_table: A metadata table whose ``Subject`` column names the cohort exactly.

    Returns:
        list[str]: Case ids.

    Raises:
        ValueError: If both selectors are given, or ``num_cases`` is outside ``1..<available>``.
        SystemExit: If the table names a case that is not in ``images_dir``.
    """
    if num_cases is not None and metadata_table is not None:
        raise ValueError("give at most one of num_cases and metadata_table")
    available = list_cases(images_dir)
    if metadata_table is not None:
        with Path(metadata_table).open(newline="") as handle:
            wanted = natsorted({row["Subject"] for row in csv.DictReader(handle)})
        if missing := [case for case in wanted if case not in available]:
            raise SystemExit(
                f"❌ {len(missing)} case(s) named by {metadata_table} are not under {images_dir}: {missing[:5]} — "
                "the published cohort was cut from the full MSD Task01 training set"
            )
        return wanted
    if num_cases is None:
        return available
    if not 1 <= num_cases <= len(available):
        raise ValueError(
            f"num_cases must be between 1 and {len(available)} (the cases under {images_dir}), got {num_cases}"
        )
    return available[:num_cases]
