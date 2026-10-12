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

"""The verification gate's comparison: what it certifies, and what it must refuse to pass.

No network here — ``fetch_published`` is the only part of the module that reaches the dataset, and
these exercise ``compare``/``source_trust_of`` on frames built in-process. The end-to-end run
against the published export is ``make -C fl-tutorials verify-<project>-omop-tables``.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

VERIFY_PATH = Path(__file__).resolve().parents[3] / "datasets" / "utils" / "verify_omop_tables.py"


@pytest.fixture(scope="module")
def verify() -> ModuleType:
    """The verify_omop_tables module, imported from its path under a unique module name."""
    module_name = "fl_tutorials_under_test.verify_omop_tables"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, VERIFY_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {VERIFY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _published() -> pd.DataFrame:
    """A published table as the dataset ships it: one file, provenance in ``source_trust``."""
    return pd.DataFrame(
        {
            "person_id": [11, 22, 33, 44],
            "gender_concept_id": [8507, 8532, 8507, 8532],
            "source_trust": [1, 1, 2, 2],
        }
    )


def _generated() -> pd.DataFrame:
    """The same table as the converters write it: per-trust files, ``source_trust`` re-derived."""
    return pd.DataFrame(
        {
            "person_id": [11, 22, 33, 44],
            "gender_concept_id": [8507, 8532, 8507, 8532],
            "source_trust": [1, 1, 2, 2],
        }
    )


def test_source_trust_is_derived_from_the_trust_directory_name(verify: ModuleType) -> None:
    """trust_1 is source_trust 1 — the mapping omop_db_tools.dataset partitions on."""
    assert verify.source_trust_of("trust_1") == 1
    assert verify.source_trust_of("trust_2") == 2


def test_source_trust_of_rejects_a_name_it_cannot_derive_from(verify: ModuleType) -> None:
    """Silently guessing a trust number would mis-attribute every row in that file."""
    with pytest.raises(SystemExit, match="cannot derive source_trust"):
        verify.source_trust_of("gstt")


def test_matching_tables_compare_equal(verify: ModuleType) -> None:
    ok, detail = verify.compare(_generated(), _published())
    assert ok, detail
    assert "4 rows x 3 cols" in detail


def test_a_repartitioned_row_fails_the_gate(verify: ModuleType) -> None:
    """The regression this column exists to catch (FLIP#1097 review).

    source_trust decides which trust's OMOP holds a person, hence which trust's cohort query
    returns them and whose imaging is pulled into XNAT. Both sides are sorted by the surrogate key
    before comparison, so a row moved between trusts changes no other cell — with the column
    dropped from both sides, as the gate once did, this frame passed.
    """
    moved = _generated()
    moved.loc[moved["person_id"] == 22, "source_trust"] = 2

    ok, detail = verify.compare(moved, _published())
    assert not ok
    assert "source_trust" in detail

    # ...and it is invisible without the column, which is why dropping it is not an option.
    ok_dropped, _ = verify.compare(moved.drop(columns=["source_trust"]), _published().drop(columns=["source_trust"]))
    assert ok_dropped


def test_a_published_table_without_source_trust_fails(verify: ModuleType) -> None:
    """An export that has lost its provenance column cannot certify the split, so it must not pass."""
    ok, detail = verify.compare(_generated(), _published().drop(columns=["source_trust"]))
    assert not ok
    assert "generated columns absent upstream: ['source_trust']" in detail


def test_empty_published_only_columns_are_excused(verify: ModuleType) -> None:
    """The published export materialises optional schema columns the converter omits."""
    theirs = _published().assign(care_site_id=[None, None, None, None], provider_id=[0, 0, 0, 0])
    ok, detail = verify.compare(_generated(), theirs)
    assert ok, detail
    assert "+2 empty published-only col(s)" in detail


def test_a_published_only_column_carrying_data_is_a_gap(verify: ModuleType) -> None:
    theirs = _published().assign(care_site_id=[7, 7, 7, 7])
    ok, detail = verify.compare(_generated(), theirs)
    assert not ok
    assert "published-only columns carry data: ['care_site_id']" in detail
