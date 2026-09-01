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

"""seed_orthanc: trust-slice selection, the zero-mismatch guard, and the upload contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "seed_orthanc.py"


@pytest.fixture(scope="module")
def seed() -> ModuleType:
    """Import the PEP 723 script as a module (it has no package)."""
    spec = importlib.util.spec_from_file_location("seed_orthanc_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestSelectAccessions:
    def test_selects_only_this_trusts_rows_in_table_order(self, seed):
        rows = [
            {"accession_id": "FAK1", "source_trust": "1"},
            {"accession_id": "FAK2", "source_trust": "2"},
            {"accession_id": "FAK3", "source_trust": "1"},
        ]
        assert seed.select_accessions(rows, 1) == ["FAK1", "FAK3"]
        assert seed.select_accessions(rows, 2) == ["FAK2"]

    def test_deduplicates_a_multi_series_study(self, seed):
        rows = [{"accession_id": "FAK1", "source_trust": "1"}] * 3
        assert seed.select_accessions(rows, 1) == ["FAK1"]

    def test_a_trust_with_no_rows_gets_nothing(self, seed):
        assert seed.select_accessions([{"accession_id": "FAK1", "source_trust": "1"}], 3) == []


class TestMissingAccessions:
    def test_reports_every_accession_without_a_directory(self, seed, tmp_path):
        (tmp_path / "FAK1").mkdir()
        (tmp_path / "FAK3").write_text("a file, not a directory")

        assert seed.missing_accessions(tmp_path, ["FAK1", "FAK2", "FAK3"]) == ["FAK2", "FAK3"]


class _Response:
    def __init__(self, status: int, body: dict | list | None = None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Session:
    """Scripted responses per call; records what was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def delete(self, url, **kwargs):
        self.deletes.append(url)
        return _Response(200)


class TestUploadInstance:
    def test_posts_application_dicom_and_returns_orthancs_status(self, seed, monkeypatch):
        session = _Session([_Response(200, {"Status": "AlreadyStored"})])

        status = seed.upload_instance(session, "http://pacs", b"DICM")

        assert status == "AlreadyStored"
        url, kwargs = session.posts[0]
        assert url == "http://pacs/instances"
        assert kwargs["headers"] == {"Content-Type": "application/dicom"}
        assert kwargs["data"] == b"DICM"

    def test_retries_a_5xx_then_succeeds(self, seed, monkeypatch):
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        session = _Session([_Response(503), _Response(200, {"Status": "Success"})])

        assert seed.upload_instance(session, "http://pacs", b"DICM") == "Success"
        assert len(session.posts) == 2

    def test_retries_a_connection_error_then_succeeds(self, seed, monkeypatch):
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        session = _Session([requests.ConnectionError("reset"), _Response(200, {"Status": "Success"})])

        assert seed.upload_instance(session, "http://pacs", b"DICM") == "Success"

    def test_a_4xx_is_raised_at_once_not_retried(self, seed, monkeypatch):
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        session = _Session([_Response(401), _Response(200, {"Status": "Success"})])

        with pytest.raises(requests.HTTPError, match="401"):
            seed.upload_instance(session, "http://pacs", b"DICM")
        assert len(session.posts) == 1

    def test_gives_up_after_the_last_attempt(self, seed, monkeypatch):
        monkeypatch.setattr(seed.time, "sleep", lambda _: None)
        session = _Session([_Response(500), _Response(500), _Response(500)])

        with pytest.raises(requests.HTTPError, match="500"):
            seed.upload_instance(session, "http://pacs", b"DICM", attempts=3)
        assert len(session.posts) == 3


class TestDeleteStudies:
    def test_finds_by_accession_and_deletes_every_hit(self, seed):
        session = _Session([_Response(200, ["study-a", "study-b"]), _Response(200, [])])

        assert seed.delete_studies(session, "http://pacs", ["FAK1", "FAK2"]) == 2
        assert session.posts[0][1]["json"] == {"Level": "Study", "Query": {"AccessionNumber": "FAK1"}}
        assert session.deletes == ["http://pacs/studies/study-a", "http://pacs/studies/study-b"]


class TestSeedProjectGuard:
    def test_refuses_to_upload_anything_when_an_accession_has_no_dicoms(self, seed, tmp_path, monkeypatch):
        """The zero-mismatch guard: OMOP says trust 1 owns FAK2, the archive has no FAK2 — stop."""
        project_dir = tmp_path / "20260729" / "cxr_project"
        (project_dir / "FAK1").mkdir(parents=True)
        (project_dir / seed.COMPLETE_MARKER).write_text("cached")
        rows = [{"accession_id": "FAK1", "source_trust": "1"}, {"accession_id": "FAK2", "source_trust": "1"}]
        monkeypatch.setattr(seed, "fetch_image_occurrence", lambda *a, **k: rows)
        session = _Session([])

        with pytest.raises(SystemExit, match="1 of trust 1's 2 accessions have no DICOM directory"):
            seed.seed_project(session, "http://pacs", "20260729", "cxr_project", 1, tmp_path, False, False)
        assert session.posts == [], "nothing may be uploaded when the slice does not resolve"

    def test_dry_run_counts_this_trusts_instances_and_uploads_nothing(self, seed, tmp_path, monkeypatch):
        project_dir = tmp_path / "20260729" / "cxr_project"
        for acc, n in (("FAK1", 2), ("FAK2", 3)):
            (project_dir / acc).mkdir(parents=True)
            for i in range(n):
                (project_dir / acc / f"{i}.dcm").write_bytes(b"DICM")
        (project_dir / seed.COMPLETE_MARKER).write_text("cached")
        rows = [{"accession_id": "FAK1", "source_trust": "1"}, {"accession_id": "FAK2", "source_trust": "2"}]
        monkeypatch.setattr(seed, "fetch_image_occurrence", lambda *a, **k: rows)
        session = _Session([])

        outcome = seed.seed_project(session, "http://pacs", "20260729", "cxr_project", 1, tmp_path, False, True)

        assert outcome == {"dry-run": 2}, "only FAK1 belongs to trust 1"
        assert session.posts == []

    def test_uploads_exactly_this_trusts_files(self, seed, tmp_path, monkeypatch):
        project_dir = tmp_path / "20260729" / "cxr_project"
        for acc in ("FAK1", "FAK2"):
            (project_dir / acc).mkdir(parents=True)
            (project_dir / acc / "0.dcm").write_bytes(acc.encode())
        (project_dir / seed.COMPLETE_MARKER).write_text("cached")
        rows = [{"accession_id": "FAK1", "source_trust": "2"}, {"accession_id": "FAK2", "source_trust": "1"}]
        monkeypatch.setattr(seed, "fetch_image_occurrence", lambda *a, **k: rows)
        session = _Session([_Response(200, {"Status": "Success"})])

        outcome = seed.seed_project(session, "http://pacs", "20260729", "cxr_project", 1, tmp_path, False, False)

        assert outcome == {"Success": 1}
        assert [kw["data"] for _, kw in session.posts] == [b"FAK2"]
