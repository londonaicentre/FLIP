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
"""Seeding the IDC pathology tutorial into running dev trusts, and reading it back.

``seed_slides.py`` posts whole-slide DICOM into each trust's Orthanc, keyed on the published manifest's
``site`` column; the matching OMOP rows reach each trust through the platform's own seed pipeline
(``make -C trust seed-omop PROJECTS=pathology_project``) from the published ``omop-csv/`` tables, which
``build_omop_project.py`` derives from that same manifest. So the tutorial can be driven through the
platform instead of only the simulator, and both stores agree because they share one input.

What is pinned here is the split. A federated demo is only honest if each trust holds its own data
and only its own; get that wrong and the run still completes, still trains, and still reports
numbers -- it is simply no longer federated. That is a silent failure, so it gets a test.

No Orthanc, no Postgres, no Hugging Face and no 6 GB of slides: the manifest is synthesised
in-process, and the builder is exercised on it end to end.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "fl-tutorials" / "datasets" / "idc_pathology"
SEEDER = DATASET_DIR / "seed_slides.py"
BUILDER = DATASET_DIR / "build_omop_project.py"
DEMO_VIDEO = REPO_ROOT / "flip-api" / "tests" / "demo_video.py"


def load_script(path: Path, name: str, stub: tuple[str, ...] = ()) -> ModuleType:
    """Import a dataset script by path, stubbing runtime-only imports the tutorial test env lacks."""
    for module_name in stub:
        sys.modules.setdefault(module_name, type(sys)(module_name))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves its own module out of sys.modules to
    # evaluate annotations; without this the decorator raises on a module that is still being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The seeder imports requests at module scope, which does not belong in the CPU-only tutorial test
# environment. Only the partitioning and the Orthanc protocol (with requests faked) are under test.
seed_slides = load_script(SEEDER, "seed_slides", stub=("requests",))
build_omop_project = load_script(BUILDER, "build_omop_project")

MANIFEST_COLUMNS = [
    "accession_id", "site", "tss", "patient_id", "slide_study_uid", "slide_series_uid",
    "slide_sop_instance_uid", "annotation_series_uid", "total_pixel_columns", "total_pixel_rows",
    "pixel_spacing_mm", "study_date", "slide_instance_mb", "annotation_series_mb", "idc_index_version",
]


def manifest_row(accession: str, site: str, study_date: str = "2024-02-17") -> dict[str, str]:
    tss = accession.split("-")[1]
    return {
        "accession_id": accession, "site": site, "tss": tss, "patient_id": accession,
        "slide_study_uid": f"2.25.{abs(hash(accession)) % 10**12}", "slide_series_uid": f"1.3.6.1.{tss}.1",
        "slide_sop_instance_uid": f"1.3.6.1.{tss}.8", "annotation_series_uid": f"1.2.826.{tss}",
        "total_pixel_columns": "35584", "total_pixel_rows": "42752", "pixel_spacing_mm": "0.0002325",
        "study_date": study_date, "slide_instance_mb": "139.8", "annotation_series_mb": "29.8",
        "idc_index_version": "24.2.2",
    }


def write_manifest(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def manifest_rows() -> list[dict[str, str]]:
    return [
        manifest_row("TCGA-A8-AAA1", "Trust_1"),
        manifest_row("TCGA-A8-AAA2", "Trust_1"),
        manifest_row("TCGA-A7-BBB1", "Trust_2"),
    ]


# --------------------------------------------------------------------------------------------
# The split: which trust receives which slide
# --------------------------------------------------------------------------------------------


def test_each_accession_goes_to_exactly_one_trust(manifest_rows: list[dict[str, str]]) -> None:
    """The property that makes the demo federated rather than merely distributed."""
    one = seed_slides.accessions_for(manifest_rows, "1")
    two = seed_slides.accessions_for(manifest_rows, "2")

    assert one == ["TCGA-A8-AAA1", "TCGA-A8-AAA2"]
    assert two == ["TCGA-A7-BBB1"]
    assert not set(one) & set(two)
    assert len(one) + len(two) == len(manifest_rows)


def test_a_trust_with_no_rows_gets_nothing(manifest_rows: list[dict[str, str]]) -> None:
    """A wrong trust number must seed nothing, not everything."""
    assert seed_slides.accessions_for(manifest_rows, "3") == []


def test_trust_number_is_the_manifest_site_number() -> None:
    """Trust_N in the manifest is OMOP source_trust N -- the one key both halves of the seed share."""
    assert seed_slides.trust_number_of("Trust_2") == "2"
    assert seed_slides.trust_number_of("Trust_02") == "2"
    with pytest.raises(ValueError, match="Trust_1"):
        seed_slides.trust_number_of("site-A")


def test_slide_path_follows_the_trust_number_not_the_row_order() -> None:
    """Slides are looked up under Trust_<n>, keyed by the same number as the manifest site."""
    path = seed_slides.slide_path(Path("/data"), "2", "BBB-1")

    assert path == Path("/data/Trust_2/accession-resources/BBB-1/slide.dcm")


def test_missing_manifest_says_how_to_fetch_it(tmp_path: Path) -> None:
    """The manifest is published, not committed, so an absent one must name the fetch target."""
    with pytest.raises(FileNotFoundError, match="fetch-idc-pathology-manifest"):
        seed_slides.read_manifest(tmp_path / "manifest.csv")


# --------------------------------------------------------------------------------------------
# The OMOP half: the published tables derive from the published manifest
# --------------------------------------------------------------------------------------------


def test_builder_partitions_by_the_manifest_site(tmp_path: Path, manifest_rows: list[dict[str, str]]) -> None:
    """Every table carries source_trust = the site number, so seed-omop's split matches the slides'."""
    pd = pytest.importorskip("pandas")
    manifest = pd.read_csv(write_manifest(tmp_path / "manifest.csv", manifest_rows), dtype={"tss": str})

    tables = build_omop_project.build_tables(manifest)

    assert set(tables) == {"person", "visit_occurrence", "procedure_occurrence", "image_occurrence"}
    assert tables["image_occurrence"].set_index("accession_id")["source_trust"].to_dict() == {
        "TCGA-A8-AAA1": 1, "TCGA-A8-AAA2": 1, "TCGA-A7-BBB1": 2,
    }
    for name, frame in tables.items():
        assert list(frame["source_trust"]) == [1, 1, 2], name
    assert tables["person"]["year_of_birth"].eq(build_omop_project.UNKNOWN_YEAR_OF_BIRTH).all()
    assert tables["image_occurrence"]["modality_concept_id"].eq(build_omop_project.MODALITY_SLIDE_MICROSCOPY).all()


def test_builder_writes_the_per_trust_layout_the_gate_and_seed_pipeline_read(
    tmp_path: Path, manifest_rows: list[dict[str, str]]
) -> None:
    """omop/trust_<N>/pathology_project/<table>.csv, the partition as a directory rather than a column.

    That is the spleen and cxr converters' layout: verify_omop_tables.py re-derives source_trust from
    the directory name and omop_db_tools.dataset build re-adds it, so the pathology chain slots into
    both without a special case.
    """
    pd = pytest.importorskip("pandas")
    manifest = pd.read_csv(write_manifest(tmp_path / "manifest.csv", manifest_rows), dtype={"tss": str})
    out = tmp_path / "omop"

    written = build_omop_project.write_per_trust(build_omop_project.build_tables(manifest), out)

    assert {p.relative_to(out).parts[0] for p in written} == {"trust_1", "trust_2"}
    assert all(p.parent.name == "pathology_project" for p in written)
    trust_1 = pd.read_csv(out / "trust_1" / "pathology_project" / "image_occurrence.csv")
    trust_2 = pd.read_csv(out / "trust_2" / "pathology_project" / "image_occurrence.csv")
    assert "source_trust" not in trust_1.columns
    assert sorted(trust_1["accession_id"]) == ["TCGA-A8-AAA1", "TCGA-A8-AAA2"]
    assert list(trust_2["accession_id"]) == ["TCGA-A7-BBB1"]


def test_builder_is_deterministic(tmp_path: Path, manifest_rows: list[dict[str, str]]) -> None:
    """Same manifest in, byte-identical tables out -- what lets the gate certify the published export."""
    manifest = write_manifest(tmp_path / "manifest.csv", manifest_rows)

    assert build_omop_project.main(["--manifest", str(manifest), "--out", str(tmp_path / "a")]) == 0
    assert build_omop_project.main(["--manifest", str(manifest), "--out", str(tmp_path / "b")]) == 0

    a = sorted((tmp_path / "a").rglob("*.csv"))
    b = sorted((tmp_path / "b").rglob("*.csv"))
    assert [p.relative_to(tmp_path / "a") for p in a] == [p.relative_to(tmp_path / "b") for p in b]
    assert all(x.read_bytes() == y.read_bytes() for x, y in zip(a, b))


def test_builder_refuses_to_run_without_a_fetched_manifest(tmp_path: Path) -> None:
    """Nothing is committed, so a missing manifest must point at the fetch target, not fail obscurely."""
    with pytest.raises(SystemExit, match="fetch-idc-pathology-manifest"):
        build_omop_project.main(["--manifest", str(tmp_path / "manifest.csv"), "--out", str(tmp_path)])


def test_seeding_nothing_anywhere_fails_loudly() -> None:
    """A no-op seed would otherwise surface much later as a pull that finds nothing to move."""
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
def trust() -> "seed_slides.Trust":
    return seed_slides.Trust(number="1", orthanc_url="http://orthanc.test")


def _accession_tree(root: Path, accession: str) -> None:
    directory = root / "Trust_1" / "accession-resources" / accession
    directory.mkdir(parents=True)
    (directory / "slide.dcm").write_bytes(b"slide")
    (directory / "annotation.dcm").write_bytes(b"annotation")


def test_orthanc_receives_the_slide_and_never_the_annotation(tmp_path, trust, monkeypatch) -> None:
    """The annotation must not be seeded: its presence fails the slide's C-MOVE, not just its own."""
    _accession_tree(tmp_path, "AAA-1")
    fake = _RecordingRequests()
    monkeypatch.setattr(seed_slides, "requests", fake)

    seed_slides.seed_orthanc(trust, ["AAA-1"], tmp_path, dry_run=False)

    assert fake.posted_files == [str(tmp_path / "Trust_1" / "accession-resources" / "AAA-1" / "slide.dcm")]
    assert not any("annotation" in name for name in fake.posted_files)


def test_prune_deletes_only_annotation_series_and_only_for_managed_accessions(tmp_path, trust, monkeypatch) -> None:
    """Pruning is scoped to ANN series of this project's accessions -- a shared dev PACS holds others."""
    fake = _RecordingRequests(find_result=["series-abc"])
    monkeypatch.setattr(seed_slides, "requests", fake)

    deleted = seed_slides.prune_annotations(trust, ["AAA-1", "AAA-2"], dry_run=False)

    assert deleted == 2
    assert [q["Query"]["Modality"] for q in fake.find_queries] == ["ANN", "ANN"]
    assert sorted(q["Query"]["AccessionNumber"] for q in fake.find_queries) == ["AAA-1", "AAA-2"]
    assert fake.deleted == ["http://orthanc.test/series/series-abc"] * 2


def test_prune_dry_run_deletes_nothing(trust, monkeypatch) -> None:
    fake = _RecordingRequests(find_result=["series-abc"])
    monkeypatch.setattr(seed_slides, "requests", fake)

    assert seed_slides.prune_annotations(trust, ["AAA-1"], dry_run=True) == 1
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


def test_finds_both_objects_in_the_nested_tree_a_trust_returns(tmp_path: Path) -> None:
    """A trust hands back XNAT's directory tree, not a flat accession folder.

    ``flip.get_by_accession_number`` returns the accession root; imaging-api writes the objects two
    levels below it, under ``scans/<scan>/resources/DICOM/files/``. A non-recursive scan of the root
    finds neither object and the run dies at "no whole-slide image found", which reads as a failed
    pull rather than a lookup that never descended. The layout below is the one observed on a live
    trust, including the scan directory's ``<id>-<series description>`` name.
    """
    data_utils = load_data_utils()
    files = tmp_path / "scans" / "1-FFPE_HE_TP_DX1" / "resources" / "DICOM" / "files"
    files.mkdir(parents=True)
    write_min_dicom(files / "2.25.180569379270056488293474013124856658992-1-1-irteys.dcm",
                    data_utils.SLIDE_SOP_CLASS)
    write_min_dicom(files / "annotation.dcm", data_utils.ANNOTATION_SOP_CLASS)

    slide = data_utils._find_by_sop_class(tmp_path, data_utils.SLIDE_SOP_CLASS, "slide.dcm")
    annotation = data_utils._find_by_sop_class(tmp_path, data_utils.ANNOTATION_SOP_CLASS, "annotation.dcm")

    assert slide is not None, "the slide must be found below the accession root, not only in it"
    assert annotation is not None
    assert slide.name.endswith("-irteys.dcm")
    assert annotation.name == "annotation.dcm"
    assert slide != annotation
