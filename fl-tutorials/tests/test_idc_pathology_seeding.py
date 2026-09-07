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
"""CPU-only tests for seeding the pathology tutorial's data into dev trusts.

``seed_trusts.py`` puts whole-slide DICOM in each trust's Orthanc and the matching OMOP rows in each
trust's database, so the tutorial can be driven through the platform instead of only the simulator.

What is pinned here is the split. A federated demo is only honest if each trust holds its own data
and only its own; get that wrong and the run still completes, still trains, and still reports
numbers -- it is simply no longer federated. That is a silent failure, so it gets a test.

No Orthanc, no Postgres and no 2 GB of slides: the CSVs are synthesised in-process, and the one test
that needs the real committed data reads only its ``source_trust`` column.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDER = REPO_ROOT / "fl-tutorials" / "datasets" / "idc_pathology" / "seed_trusts.py"
OMOP_DIR = REPO_ROOT / "fl-tutorials" / "datasets" / "idc_pathology" / "omop" / "pathology_project"
DEMO_VIDEO = REPO_ROOT / "flip-api" / "tests" / "demo_video.py"


def load_seeder():
    """Import the seeder without its optional runtime dependencies being installed.

    It imports psycopg2 and requests at module scope, neither of which belongs in the CPU-only
    tutorial test environment. Only the pure partitioning helpers are under test, so the two are
    stubbed rather than installed.
    """
    for name in ("psycopg2", "requests"):
        sys.modules.setdefault(name, type(sys)(name))
    spec = importlib.util.spec_from_file_location("seed_trusts", SEEDER)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves its own module out of sys.modules to
    # evaluate annotations; without this the decorator raises on a module that is still being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seed_trusts = load_seeder()


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def image_rows() -> list[dict[str, str]]:
    return [
        {"image_occurrence_id": "4000001", "accession_id": "AAA-1", "source_trust": "1"},
        {"image_occurrence_id": "4000002", "accession_id": "AAA-2", "source_trust": "1"},
        {"image_occurrence_id": "4000003", "accession_id": "BBB-1", "source_trust": "2"},
    ]


def test_each_row_goes_to_exactly_one_trust(image_rows: list[dict[str, str]]) -> None:
    """The property that makes the demo federated rather than merely distributed."""
    one = seed_trusts.rows_for(image_rows, "1")
    two = seed_trusts.rows_for(image_rows, "2")

    assert [r["accession_id"] for r in one] == ["AAA-1", "AAA-2"]
    assert [r["accession_id"] for r in two] == ["BBB-1"]
    assert not {r["image_occurrence_id"] for r in one} & {r["image_occurrence_id"] for r in two}
    assert len(one) + len(two) == len(image_rows)


def test_a_trust_with_no_rows_gets_nothing(image_rows: list[dict[str, str]]) -> None:
    """A wrong trust number must seed nothing, not everything."""
    assert seed_trusts.rows_for(image_rows, "3") == []


def test_slide_path_follows_the_trust_number_not_the_row_order() -> None:
    """Slides are looked up under Trust_<n>, keyed by the same number as the OMOP column."""
    path = seed_trusts.slide_path(Path("/data"), "2", "BBB-1")

    assert path == Path("/data/Trust_2/accession-resources/BBB-1/slide.dcm")


def test_committed_data_splits_across_both_dev_trusts() -> None:
    """The real CSVs must actually be partitioned, or the demo silently runs single-site.

    Read from the committed data rather than a fixture: a regeneration that tagged every row to one
    trust would leave every synthetic test above passing.
    """
    with (OMOP_DIR / "image_occurrence.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    by_trust: dict[str, set[str]] = {}
    for row in rows:
        by_trust.setdefault(row["source_trust"], set()).add(row["accession_id"])

    assert set(by_trust) == {"1", "2"}, f"expected both dev trusts, got {sorted(by_trust)}"
    assert not by_trust["1"] & by_trust["2"], "an accession is claimed by both trusts"
    assert all(by_trust.values()), "a trust was left with no slides"


def test_omop_rows_are_inserted_without_the_partition_column() -> None:
    """source_trust marks the mock data's split; the trust's own OMOP schema has no such column."""
    source = SEEDER.read_text()

    assert 'c != TRUST_COLUMN' in source, "the partition marker must be dropped before insert"
    assert "ON CONFLICT DO NOTHING" in source, (
        "seeding is a documented prerequisite people re-run; a second run must be a no-op"
    )


def test_seeding_nothing_anywhere_fails_loudly() -> None:
    """A no-op seed would otherwise surface much later as a cohort query that matches nothing."""
    source = SEEDER.read_text()

    assert "if not seeded_any:" in source
    assert "return 1" in source


def test_recorder_knows_the_digipath_tutorial() -> None:
    """The recorder profile must name paths that exist, in the tree it will upload from."""
    source = DEMO_VIDEO.read_text()

    assert '"digipath": {' in source
    for fragment in ("idc_pathology_nuclei_detection_evaluation/app_files",
                     "idc_pathology_nuclei_detection_evaluation/query.sql"):
        assert fragment.split("/")[-1] in source
    tutorial = REPO_ROOT / "fl-tutorials/nvflare/image_evaluation/idc_pathology_nuclei_detection_evaluation"
    assert (tutorial / "app_files").is_dir()
    assert (tutorial / "query.sql").is_file()
