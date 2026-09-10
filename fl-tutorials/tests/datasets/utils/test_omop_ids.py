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

"""The per-project surrogate-key blocks: no cross-project collisions, no accidental overflow."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

IDS_PATH = Path(__file__).resolve().parents[3] / "datasets" / "utils" / "omop_ids.py"


@pytest.fixture(scope="module")
def omop_ids() -> ModuleType:
    """The omop_ids module, imported from its path under a unique module name."""
    module_name = "fl_tutorials_under_test.omop_ids"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, IDS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {IDS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def test_every_known_project_has_a_distinct_block(omop_ids: ModuleType) -> None:
    """cxr, spleen and prostate must each get their own non-overlapping block."""
    blocks = omop_ids.PROJECT_ID_BLOCKS
    assert blocks["cxr_project"] == 1_000_000
    assert blocks["spleen_project"] == 2_000_000
    assert blocks["prostate_project"] == 3_000_000
    assert len(set(blocks.values())) == len(blocks), "block bases must be distinct"


def test_surrogate_ids_starts_one_above_the_project_base(omop_ids: ModuleType) -> None:
    assert omop_ids.surrogate_ids("cxr_project", 5) == range(1_000_001, 1_000_006)
    assert omop_ids.surrogate_ids("prostate_project", 3) == range(3_000_001, 3_000_004)


def test_surrogate_ids_spleen_regression(omop_ids: ModuleType) -> None:
    """Pins the exact range the converter has always produced for the published 41-row export.

    Was ``range(2000001, len(df) + 2000001)`` with ``len(df) == 41`` before this refactor — the
    published OMOP export was generated from that literal, so this value must never change.
    """
    assert omop_ids.surrogate_ids("spleen_project", 41) == range(2000001, 2000042)


def test_surrogate_ids_unknown_project_raises_key_error(omop_ids: ModuleType) -> None:
    """No fallback to another project's block — they share a database, so a silent default collides."""
    with pytest.raises(KeyError):
        omop_ids.surrogate_ids("prostate_project_typo", 1)


def test_surrogate_ids_overflow_raises_value_error(omop_ids: ModuleType) -> None:
    with pytest.raises(ValueError, match="overflow"):
        omop_ids.surrogate_ids("spleen_project", omop_ids.BLOCK_SIZE + 1)


def test_surrogate_ids_exactly_fills_the_block_without_error(omop_ids: ModuleType) -> None:
    """The last id in a full block is the next project's base value, which that project never uses
    (its own first id is base + 1) — so filling the block exactly is safe, not an overflow."""
    ids = omop_ids.surrogate_ids("spleen_project", omop_ids.BLOCK_SIZE)
    assert len(ids) == omop_ids.BLOCK_SIZE
    assert ids.start == 2_000_001
    assert ids.stop == 3_000_001


def test_derived_bands_do_not_intersect_any_allocated_block(omop_ids: ModuleType) -> None:
    """The point of recording a derived band: a future block base must not be picked inside one.

    cxr's image_feature_id is derived from its image_occurrence_id rather than allocated, so it
    lands outside every reserved block. Allocating a new project a base that fell inside that band
    would collide in a database all these projects share, and nothing else would catch it — the
    allocator has no idea the band exists unless it is written down here.
    """
    for (project, column), (low, high) in omop_ids.DERIVED_ID_BANDS.items():
        assert low <= high, f"{project}.{column}: band bounds are inverted"
        for other, base in omop_ids.PROJECT_ID_BLOCKS.items():
            allocated_low, allocated_high = base + 1, base + omop_ids.BLOCK_SIZE
            assert allocated_high < low or allocated_low > high, (
                f"{project}.{column} band {low}..{high} intersects {other}'s allocated block "
                f"{allocated_low}..{allocated_high}"
            )


def test_cxr_derived_band_matches_the_scheme_that_produces_it(omop_ids: ModuleType) -> None:
    """Pins the band to omop_convert_cxr's ``int(f"{image_occurrence_id}{entry_id:02d}")``.

    Derived from cxr's block edges rather than from the current dataset, so re-running the converter
    on more rows cannot silently leave the recorded band behind.
    """
    base = omop_ids.PROJECT_ID_BLOCKS["cxr_project"]
    first_occurrence, last_occurrence = base + 1, base + omop_ids.BLOCK_SIZE
    expected = (int(f"{first_occurrence}{0:02d}"), int(f"{last_occurrence}{99:02d}"))

    assert omop_ids.DERIVED_ID_BANDS[("cxr_project", "image_feature_id")] == expected

    # The published 20260901 export sits inside it: 8332 occurrences, at most two findings each.
    low, high = expected
    assert low <= 100_000_100
    assert high >= 100_833_201
