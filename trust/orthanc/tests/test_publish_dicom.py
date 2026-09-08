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

"""publish_dicom: the both-ways verification and where the tables it checks against come from."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "publish_dicom.py"


@pytest.fixture(scope="module")
def pub() -> ModuleType:
    spec = importlib.util.spec_from_file_location("publish_dicom_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def canonical_tree(root: Path) -> Path:
    write_csv(
        root / "prostate_project" / "image_occurrence.csv",
        [
            {"accession_id": "10000_1000000", "image_study_uid": "1.2.3", "source_trust": "1"},
            {"accession_id": "10001_1000001", "image_study_uid": "1.2.4", "source_trust": "2"},
        ],
    )
    write_csv(
        root / "prostate_project" / "person.csv",
        [{"person_source_value": "10000"}, {"person_source_value": "10001"}],
    )
    return root


def instance(pub, accession: str, study: str, patient: str, sop: str):
    return pub.Instance(
        member=f"{accession}/{sop}.dcm", accession=accession, study_uid=study, patient_id=patient, sop_uid=sop
    )


class TestLoadTables:
    def test_reads_a_local_canonical_tree(self, pub, tmp_path):
        tables = pub.load_tables("prostate_project", None, canonical_tree(tmp_path))
        assert [r["accession_id"] for r in tables["image_occurrence"]] == ["10000_1000000", "10001_1000001"]
        assert [r["person_source_value"] for r in tables["person"]] == ["10000", "10001"]

    def test_a_missing_local_table_is_named(self, pub, tmp_path):
        with pytest.raises(SystemExit, match="prostate_project/image_occurrence.csv"):
            pub.load_tables("prostate_project", None, tmp_path)

    def test_exactly_one_source(self, pub, tmp_path):
        with pytest.raises(SystemExit, match="exactly one"):
            pub.load_tables("prostate_project", None, None)
        with pytest.raises(SystemExit, match="exactly one"):
            pub.load_tables("prostate_project", "20260729", tmp_path)


class TestPackage:
    def test_archive_paths_are_accession_then_sop_so_multi_series_studies_cannot_collide(self, pub, tmp_path):
        """Three series of one study all start at 0000.dcm; the archive must not care."""
        source_dir = tmp_path / "src"
        members = {}
        for series, sop in (("t2w", "1.1"), ("adc", "1.2"), ("hbv", "1.3")):
            path = source_dir / "10000" / "1000000" / series / "0000.dcm"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"DICM" + series.encode())
            members[series] = str(path.relative_to(source_dir))
        instances = [instance(pub, "10000_1000000", "1.2.3", "10000", sop) for sop in ("1.1", "1.2", "1.3")]
        for inst, series in zip(instances, ("t2w", "adc", "hbv")):
            inst.member = members[series]

        assert [pub.arcname(i) for i in instances] == [
            "10000_1000000/1.1.dcm",
            "10000_1000000/1.2.dcm",
            "10000_1000000/1.3.dcm",
        ]
        out = tmp_path / "out.tar.gz"
        written, filled = pub.package(pub.Source(source_dir), instances, out, fill=False)
        assert (written, filled) == (3, 0)
        import tarfile

        with tarfile.open(out) as tar:
            assert sorted(tar.getnames()) == ["10000_1000000/1.1.dcm", "10000_1000000/1.2.dcm", "10000_1000000/1.3.dcm"]
            assert tar.extractfile("10000_1000000/1.2.dcm").read() == b"DICMadc"


class TestVerify:
    def test_exact_match_passes_and_reports_the_split(self, pub, tmp_path):
        tables = pub.load_tables("prostate_project", None, canonical_tree(tmp_path))
        instances = [
            instance(pub, "10000_1000000", "1.2.3", "10000", "s1"),
            instance(pub, "10000_1000000", "1.2.3", "10000", "s2"),
            instance(pub, "10001_1000001", "1.2.4", "10001", "s3"),
        ]
        ok, per_trust = pub.verify(instances, tables)
        assert ok
        assert per_trust == {"1": 1, "2": 1}

    def test_a_published_study_missing_from_the_source_fails(self, pub, tmp_path, capsys):
        tables = pub.load_tables("prostate_project", None, canonical_tree(tmp_path))
        ok, _ = pub.verify([instance(pub, "10000_1000000", "1.2.3", "10000", "s1")], tables)
        assert not ok
        assert "1 published accession(s) have no DICOM" in capsys.readouterr().out

    def test_an_unpublished_study_in_the_source_fails(self, pub, tmp_path, capsys):
        tables = pub.load_tables("prostate_project", None, canonical_tree(tmp_path))
        instances = [
            instance(pub, "10000_1000000", "1.2.3", "10000", "s1"),
            instance(pub, "10001_1000001", "1.2.4", "10001", "s2"),
            instance(pub, "10002_1000002", "1.2.5", "10002", "s3"),
        ]
        ok, _ = pub.verify(instances, tables)
        assert not ok
        out = capsys.readouterr().out
        assert "not published" in out
        assert "StudyInstanceUID(s) not published" in out

    def test_a_duplicate_sop_fails(self, pub, tmp_path, capsys):
        tables = pub.load_tables("prostate_project", None, canonical_tree(tmp_path))
        instances = [
            instance(pub, "10000_1000000", "1.2.3", "10000", "same"),
            instance(pub, "10001_1000001", "1.2.4", "10001", "same"),
        ]
        ok, _ = pub.verify(instances, tables)
        assert not ok
        assert "duplicate SOPInstanceUID" in capsys.readouterr().out
