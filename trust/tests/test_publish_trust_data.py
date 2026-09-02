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

"""publish_trust_data: the single-copy layout, the one-commit-one-tag contract, and its refusals."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "publish_trust_data.py"


@pytest.fixture(scope="module")
def pub() -> ModuleType:
    """Import the PEP 723 script as a module (it has no package)."""
    spec = importlib.util.spec_from_file_location("publish_trust_data_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeApi:
    """Records what the publisher asks of the Hub; answers with a fixed set of existing tags."""

    def __init__(self, tags: list[str]):
        self.tags = tags
        self.commits: list[dict] = []
        self.created_tags: list[dict] = []

    def list_repo_refs(self, repo, repo_type):
        return SimpleNamespace(tags=[SimpleNamespace(name=t) for t in self.tags])

    def create_commit(self, repo, repo_type, operations, commit_message, commit_description):
        self.commits.append({"repo": repo, "operations": operations, "message": commit_message})
        return SimpleNamespace(oid=f"sha-{len(self.commits)}")

    def create_tag(self, repo, tag, revision, repo_type, tag_message):
        self.created_tags.append({"tag": tag, "revision": revision})


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


class TestLayout:
    def test_volumes_land_under_their_trust(self, pub, tmp_path):
        assert pub.path_in_repo(tmp_path / "trust1_pgdata.tar", "volume") == "trust1/trust1_pgdata.tar"
        assert pub.path_in_repo(tmp_path / "trust2_orthanc_data.tar", "volume") == "trust2/trust2_orthanc_data.tar"

    def test_dicom_sets_and_the_card(self, pub, tmp_path):
        assert pub.path_in_repo(tmp_path / "prostate_project.tar.gz", "dicom") == "dicom/prostate_project.tar.gz"
        assert pub.path_in_repo(tmp_path / "README.md", "card") == "README.md"

    def test_a_versioned_filename_is_refused(self, pub, tmp_path):
        """The version is the tag; a suffixed file would be the old layout's second copy."""
        with pytest.raises(SystemExit, match="version is the tag"):
            pub.path_in_repo(tmp_path / "trust1_pgdata_20260729.tar", "volume")
        with pytest.raises(SystemExit, match="version is the tag"):
            pub.path_in_repo(tmp_path / "cxr_project_20260729.tar.gz", "dicom")

    def test_an_unknown_name_is_refused(self, pub, tmp_path):
        with pytest.raises(SystemExit, match="trust<N>_pgdata.tar"):
            pub.path_in_repo(tmp_path / "backup.tar", "volume")
        with pytest.raises(SystemExit, match="dicom/<project>.tar.gz"):
            pub.path_in_repo(tmp_path / "prostate_project.zip", "dicom")

    def test_canonical_tree_maps_project_by_project(self, pub, tmp_path):
        canonical = tmp_path / "canonical"
        touch(canonical / "spleen_project" / "person.csv")
        touch(canonical / "spleen_project" / "source" / "dicom_metadata.csv")
        touch(canonical / "cxr_project" / "observation.csv")

        ops = pub.omop_csv_operations(canonical)

        assert [op.path_in_repo for op in ops] == [
            "omop-csv/cxr_project/observation.csv",
            "omop-csv/spleen_project/person.csv",
            "omop-csv/spleen_project/source/dicom_metadata.csv",
        ]

    def test_a_flat_csv_dir_is_refused(self, pub, tmp_path):
        touch(tmp_path / "person.csv")
        with pytest.raises(SystemExit, match="<project>/<table>.csv"):
            pub.omop_csv_operations(tmp_path)


class TestBuildOperations:
    def test_only_what_is_passed_is_published(self, pub, tmp_path):
        pg = touch(tmp_path / "trust1_pgdata.tar")
        ops = pub.build_operations([pg], None, [], None)
        assert [op.path_in_repo for op in ops] == ["trust1/trust1_pgdata.tar"]

    def test_a_missing_local_file_is_refused_before_anything_uploads(self, pub, tmp_path):
        with pytest.raises(SystemExit, match="missing local file"):
            pub.build_operations([tmp_path / "trust1_pgdata.tar"], None, [], None)

    def test_nothing_to_publish_is_refused(self, pub):
        with pytest.raises(SystemExit, match="nothing to publish"):
            pub.build_operations([], None, [], None)


class TestPublish:
    def test_one_commit_then_the_tag_on_that_commit(self, pub, tmp_path):
        api = FakeApi(tags=["20260729"])
        ops = pub.build_operations([touch(tmp_path / "trust1_pgdata.tar")], None, [], touch(tmp_path / "README.md"))

        oid = pub.publish(api, "20261001", ops, "org/data", dry_run=False)

        assert oid == "sha-1"
        assert len(api.commits) == 1
        assert [op.path_in_repo for op in api.commits[0]["operations"]] == ["trust1/trust1_pgdata.tar", "README.md"]
        assert api.commits[0]["message"] == "trust-data 20261001: 2 file(s)"
        assert api.created_tags == [{"tag": "20261001", "revision": "sha-1"}]

    def test_an_existing_tag_is_never_moved(self, pub, tmp_path):
        api = FakeApi(tags=["20260729", "20260901"])
        ops = pub.build_operations([touch(tmp_path / "trust1_pgdata.tar")], None, [], None)

        with pytest.raises(SystemExit, match="already exists"):
            pub.publish(api, "20260901", ops, "org/data", dry_run=False)
        assert api.commits == []
        assert api.created_tags == []

    def test_dry_run_uploads_and_tags_nothing(self, pub, tmp_path, capsys):
        api = FakeApi(tags=[])
        ops = pub.build_operations([touch(tmp_path / "trust2_orthanc_data.tar")], None, [], None)

        assert pub.publish(api, "20261001", ops, "org/data", dry_run=True) is None
        assert api.commits == []
        assert api.created_tags == []
        assert "trust2/trust2_orthanc_data.tar" in capsys.readouterr().out
