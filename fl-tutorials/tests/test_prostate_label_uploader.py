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

"""The prostate label uploader: the identity mapping, the two passes, and the report gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from tutorial_apps import TUTORIALS_ROOT

SCRIPT = TUTORIALS_ROOT / "datasets" / "prostate" / "upload_prostate_labels_to_xnat.py"


@pytest.fixture(scope="module")
def uploader() -> ModuleType:
    spec = importlib.util.spec_from_file_location("upload_prostate_labels_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def labels(root: Path, name: str, accessions: list[str]) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    for accession in accessions:
        (directory / f"{accession}.nii.gz").write_bytes(b"x")
    return directory


class TestBuildManifest:
    def test_every_label_file_is_an_item_addressed_by_its_own_stem(self, uploader, tmp_path):
        items = uploader.build_manifest(labels(tmp_path, "labels", ["10540_1000550", "10000_1000000"]))
        assert [(i.accession_id, i.file_path.name) for i in items] == [
            ("10000_1000000", "10000_1000000.nii.gz"),
            ("10540_1000550", "10540_1000550.nii.gz"),
        ]
        assert all(i.target_filename is None for i in items), "the name is derived from the image in the scan"

    def test_an_accession_filter_keeps_only_the_named_studies(self, uploader, tmp_path):
        directory = labels(tmp_path, "labels", ["10000_1000000", "10001_1000001"])
        items = uploader.build_manifest(directory, {"10001_1000001"})
        assert [i.accession_id for i in items] == ["10001_1000001"]

    def test_a_missing_directory_points_at_the_download(self, uploader, tmp_path):
        with pytest.raises(SystemExit, match="download-prostate-data"):
            uploader.build_manifest(tmp_path / "nowhere")

    def test_an_empty_directory_is_refused(self, uploader, tmp_path):
        (tmp_path / "labels").mkdir()
        with pytest.raises(SystemExit, match="No <patient>_<study>.nii.gz"):
            uploader.build_manifest(tmp_path / "labels")


class TestMain:
    def test_two_passes_with_two_prefixes_and_the_worse_exit_code_wins(self, uploader, tmp_path, monkeypatch):
        labels(tmp_path, "labels", ["10000_1000000"])
        labels(tmp_path, "zonal_labels", ["10000_1000000"])
        calls: list[dict] = []
        codes = iter([0, 1])

        def fake_run_enrichment(clients, items, **kwargs):
            calls.append({"items": [i.accession_id for i in items], **kwargs})
            code = next(codes)
            return SimpleNamespace(render=lambda: "report", exit_code=lambda **_: code)

        monkeypatch.setattr(uploader, "run_enrichment", fake_run_enrichment)
        monkeypatch.setattr(uploader, "build_clients", lambda args: ["client"])

        code = uploader.main(
            [
                "--flip-project-id", "abc", "--labels-dir", str(tmp_path / "labels"),
                "--zonal-labels-dir", str(tmp_path / "zonal_labels"), "--xnat-url", "http://x",
                "--xnat-user", "u", "--xnat-password", "p", "--dry-run",
            ]  # fmt: skip
        )

        assert code == 1
        assert [c["rename"] for c in calls] == [uploader.WHOLE_GLAND_RENAME, uploader.ZONAL_RENAME]
        assert all(c["items"] == ["10000_1000000"] and c["dry_run"] and c["flip_project_id"] == "abc" for c in calls)

    def test_whole_gland_only_when_no_zonal_dir(self, uploader, tmp_path, monkeypatch):
        labels(tmp_path, "labels", ["10000_1000000"])
        calls: list[dict] = []

        def fake_run_enrichment(clients, items, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(render=lambda: "", exit_code=lambda **_: 0)

        monkeypatch.setattr(uploader, "run_enrichment", fake_run_enrichment)
        monkeypatch.setattr(uploader, "build_clients", lambda args: [])

        code = uploader.main(
            [
                "--flip-project-id", "abc", "--labels-dir", str(tmp_path / "labels"),
                "--xnat-url", "http://x", "--xnat-user", "u", "--xnat-password", "p",
            ]  # fmt: skip
        )

        assert code == 0
        assert [c["rename"] for c in calls] == [uploader.WHOLE_GLAND_RENAME]
