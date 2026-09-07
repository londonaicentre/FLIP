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


def test_reseeding_replaces_rather_than_accumulates() -> None:
    """Re-resolving the manifest renumbers every id, so conflict-skipping does not deduplicate.

    Keyed on the patient barcode instead. Pinned because the failure is a doubled cohort, not an
    error: the demo would pull and score each slide twice and still report plausible numbers.
    """
    source = SEEDER.read_text()

    assert "ON CONFLICT" not in source, "surrogate ids are not stable across a manifest re-resolve"
    assert "person_source_value = ANY" in source, "the delete must be scoped by the natural key"
    assert "DELETE FROM omop.person WHERE person_id = ANY" in source


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


# --------------------------------------------------------------------------------------------
# Reading the seeded data back on a trust
# --------------------------------------------------------------------------------------------
#
# The seeding above and this lookup are two halves of one path, which is why they share a file. A
# trust does not hand back the tutorial's filenames: imaging-api delivers whatever XNAT stored, named
# by SOP Instance UID. The app therefore identifies the slide and its annotations by SOP Class.


def load_data_utils():
    """Import app_files/data_utils.py, stubbing the flip package the FL client provides at runtime."""
    for name, attrs in (
        ("flip", {}),
        ("flip.constants", {"FlipConstants": type("FlipConstants", (), {"LOCAL_DEV": False}),
                            "ResourceType": type("ResourceType", (), {"DICOM": "DICOM"})}),
    ):
        module = sys.modules.setdefault(name, type(sys)(name))
        for attr, value in attrs.items():
            setattr(module, attr, value)

    app_files = REPO_ROOT / "fl-tutorials/nvflare/image_evaluation/idc_pathology_nuclei_detection_evaluation/app_files"
    spec = importlib.util.spec_from_file_location("idc_seeding_data_utils", app_files / "data_utils.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(app_files))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(app_files))
    return module


def write_min_dicom(path: Path, sop_class_uid: str) -> None:
    pydicom = pytest.importorskip("pydicom")
    from pydicom.dataset import FileMetaDataset

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class_uid
    meta.MediaStorageSOPInstanceUID = "1.2.3.4"
    meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds = pydicom.dataset.FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = "1.2.3.4"
    ds.save_as(path, enforce_file_format=True)


def test_finds_both_objects_under_xnat_style_names(tmp_path: Path) -> None:
    """The real failure mode: correct data, trust-assigned filenames, nothing found."""
    data_utils = load_data_utils()
    write_min_dicom(tmp_path / "1.2.840.113619.2.1.dcm", data_utils.SLIDE_SOP_CLASS)
    write_min_dicom(tmp_path / "1.2.840.113619.2.2.dcm", data_utils.ANNOTATION_SOP_CLASS)

    slide = data_utils._find_by_sop_class(tmp_path, data_utils.SLIDE_SOP_CLASS, "slide.dcm")
    annotation = data_utils._find_by_sop_class(tmp_path, data_utils.ANNOTATION_SOP_CLASS, "annotation.dcm")

    assert slide is not None
    assert annotation is not None
    assert slide.name == "1.2.840.113619.2.1.dcm"
    assert annotation.name == "1.2.840.113619.2.2.dcm"
    assert slide != annotation, "the two objects must not resolve to the same file"


def test_still_finds_the_tutorials_own_filenames(tmp_path: Path) -> None:
    """The LOCAL_DEV layout must keep working unchanged."""
    data_utils = load_data_utils()
    write_min_dicom(tmp_path / "slide.dcm", data_utils.SLIDE_SOP_CLASS)
    write_min_dicom(tmp_path / "annotation.dcm", data_utils.ANNOTATION_SOP_CLASS)

    assert data_utils._find_by_sop_class(tmp_path, data_utils.SLIDE_SOP_CLASS, "slide.dcm").name == "slide.dcm"


def test_ignores_unrelated_files_rather_than_failing(tmp_path: Path) -> None:
    """A pull can deliver a whole study; non-DICOM neighbours must not break the scan."""
    data_utils = load_data_utils()
    (tmp_path / "catalog.xml").write_text("<not-dicom/>")
    write_min_dicom(tmp_path / "anything.dcm", data_utils.SLIDE_SOP_CLASS)

    assert data_utils._find_by_sop_class(tmp_path, data_utils.SLIDE_SOP_CLASS, "slide.dcm") is not None
    assert data_utils._find_by_sop_class(tmp_path, data_utils.ANNOTATION_SOP_CLASS, "annotation.dcm") is None


# ---------------------------------------------------------------------------
# How the two DICOM objects reach a trust.
#
# The slide travels by C-MOVE; the annotation cannot, and must not be put where one would be
# attempted. XNAT's DICOM receiver (dcm4che 2.0.29) has no presentation context for the Microscopy
# Bulk Simple Annotations SOP class, and because slide and annotation share an accession, an
# annotation sitting in Orthanc fails the *whole study's* C-MOVE -- so the slide never arrives
# either, and the study wedges in ISSUED while the status endpoint reports `Processing` forever.
#
# That failure is invisible from the pull's own counters and looks exactly like a slow whole-slide
# transfer, which is why it is pinned by a test rather than left to a comment.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _RecordingRequests:
    """Minimal stand-in for the ``requests`` module, recording what the seeder would send."""

    def __init__(self, find_result=None):
        self.posted_urls: list[str] = []
        self.posted_files: list[str] = []
        self.find_queries: list[dict] = []
        self.deleted: list[str] = []
        self._find_result = find_result or []

    def post(self, url, data=None, json=None, **kwargs):
        if url.endswith("/tools/find"):
            self.find_queries.append(json)
            return _FakeResponse(self._find_result)
        self.posted_urls.append(url)
        self.posted_files.append(getattr(data, "name", str(data)))
        return _FakeResponse({"Status": "Success"})

    def delete(self, url, **kwargs):
        self.deleted.append(url)
        return _FakeResponse()


@pytest.fixture
def trust() -> "seed_trusts.Trust":
    return seed_trusts.Trust(number="1", orthanc_url="http://orthanc.test", omop_dsn="postgresql://x")


def _accession_tree(root: Path, accession: str) -> None:
    directory = root / "Trust_1" / "accession-resources" / accession
    directory.mkdir(parents=True)
    (directory / "slide.dcm").write_bytes(b"slide")
    (directory / "annotation.dcm").write_bytes(b"annotation")


def test_orthanc_receives_the_slide_and_never_the_annotation(tmp_path, trust, monkeypatch) -> None:
    """The annotation must not be seeded: its presence fails the slide's C-MOVE, not just its own."""
    _accession_tree(tmp_path, "AAA-1")
    fake = _RecordingRequests()
    monkeypatch.setattr(seed_trusts, "requests", fake)

    seed_trusts.seed_orthanc(trust, ["AAA-1"], tmp_path, dry_run=False)

    assert fake.posted_files == [str(tmp_path / "Trust_1" / "accession-resources" / "AAA-1" / "slide.dcm")]
    assert not any("annotation" in name for name in fake.posted_files)


def test_prune_deletes_only_annotation_series_and_only_for_managed_accessions(tmp_path, trust, monkeypatch) -> None:
    """Pruning is scoped to ANN series of this project's accessions -- a shared dev PACS holds others."""
    fake = _RecordingRequests(find_result=["series-abc"])
    monkeypatch.setattr(seed_trusts, "requests", fake)

    deleted = seed_trusts.prune_annotations(trust, ["AAA-1", "AAA-2"], dry_run=False)

    assert deleted == 2
    assert [q["Query"]["Modality"] for q in fake.find_queries] == ["ANN", "ANN"]
    assert sorted(q["Query"]["AccessionNumber"] for q in fake.find_queries) == ["AAA-1", "AAA-2"]
    assert fake.deleted == ["http://orthanc.test/series/series-abc"] * 2


def test_prune_dry_run_deletes_nothing(trust, monkeypatch) -> None:
    fake = _RecordingRequests(find_result=["series-abc"])
    monkeypatch.setattr(seed_trusts, "requests", fake)

    assert seed_trusts.prune_annotations(trust, ["AAA-1"], dry_run=True) == 1
    assert fake.deleted == []


def test_recorder_carries_the_enrichment_step_for_digipath() -> None:
    """The annotations must not depend on the operator remembering a flag.

    Forgetting enrichment does not fail loudly at the point of omission: the run pulls, archives and
    starts training, then fails at scoring with nothing to compare against. So the profile carries
    the step itself, and this pins that it names a make target the datasets Makefile actually
    defines -- a rename on either side would otherwise surface only as a failed recording.
    """
    source = DEMO_VIDEO.read_text()
    digipath = source[source.index('"digipath": {'):source.index('"spleen": {')]

    assert '"enrichment"' in digipath, "the digipath profile must carry its own enrichment step"
    # The exact quoted value, not a substring: a suffix typo in the target name still contains
    # the correct name, so `in` passes while the recipe it invokes does not exist.
    assert '"make_target": "upload-idc-pathology-annotations"' in digipath

    makefile = (REPO_ROOT / "fl-tutorials" / "datasets" / "Makefile").read_text()
    assert "\nupload-idc-pathology-annotations:" in makefile, "the profile names a target that must exist"


def test_recorder_xnat_url_is_the_web_port_not_the_dicom_scp_port() -> None:
    """Segment 3 talks HTTP to XNAT, so its default must be XNAT_WEB_PORT.

    Since FLIP#993 split the two, 8104 is the DICOM SCP receiver and answers no HTTP at all, so the
    old default made the XNAT/OHIF segment unreachable rather than merely wrong.
    """
    source = DEMO_VIDEO.read_text()
    assert '"--xnat-url",\n        default="http://127.0.0.1:8105"' in source
