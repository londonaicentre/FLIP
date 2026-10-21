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

"""freeze_docs_gifs: which recording a release gets, the never-moved tag, and the release.yml wiring."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import REPO_ROOT
from huggingface_hub.errors import EntryNotFoundError

RELEASE = "a" * 40


class FakeApi:
    """A dataset whose ``main`` holds ``recordings`` (newest first): dataset commit -> source commit, or None."""

    def __init__(self, tmp_path: Path, recordings: dict[str, str | None], tags: list[str] | None = None):
        self.tmp_path = tmp_path
        self.recordings = recordings
        self.tags = tags or []
        self.created_tags: list[dict] = []

    def list_repo_refs(self, repo, repo_type):
        return SimpleNamespace(tags=[SimpleNamespace(name=t) for t in self.tags])

    def list_repo_commits(self, repo, repo_type):
        return [SimpleNamespace(commit_id=commit) for commit in self.recordings]

    def hf_hub_download(self, repo, filename, repo_type, revision):
        source = self.recordings[revision]
        if source is None:
            raise EntryNotFoundError("no manifest")
        path = self.tmp_path / f"{revision}.json"
        path.write_text(json.dumps({"source_commit": source}))
        return str(path)

    def create_tag(self, repo, tag, revision, repo_type, tag_message):
        self.created_tags.append({"tag": tag, "revision": revision})


@pytest.fixture
def released(freezer, monkeypatch):
    """Pretend the release contains exactly these source commits."""
    contained: set[str] = set()
    monkeypatch.setattr(freezer, "contains", lambda release, commit: release == RELEASE and commit in contained)
    monkeypatch.setattr(freezer, "release_tag", lambda: "v1.2.3")
    return contained


def run(freezer, monkeypatch, api: FakeApi) -> int:
    monkeypatch.setattr(freezer, "HfApi", lambda: api)
    return freezer.main(["--release-commit", RELEASE])


def test_the_release_gets_the_newest_recording_it_contains(freezer, monkeypatch, tmp_path, released):
    released.update({"src-old", "src-mid"})
    api = FakeApi(tmp_path, {"hf-new": "src-after-release", "hf-mid": "src-mid", "hf-old": "src-old"})
    assert run(freezer, monkeypatch, api) == 0
    assert api.created_tags == [{"tag": "v1.2.3", "revision": "hf-mid"}]


def test_commits_without_a_manifest_are_skipped(freezer, monkeypatch, tmp_path, released):
    released.add("src")
    api = FakeApi(tmp_path, {"hf-card-only": None, "hf-rec": "src"})
    assert run(freezer, monkeypatch, api) == 0
    assert api.created_tags == [{"tag": "v1.2.3", "revision": "hf-rec"}]


def test_an_existing_tag_is_never_moved(freezer, monkeypatch, tmp_path, released, capsys):
    released.add("src")
    api = FakeApi(tmp_path, {"hf-rec": "src"}, tags=["v1.2.3"])
    assert run(freezer, monkeypatch, api) == 0
    assert api.created_tags == []
    assert "already has v1.2.3" in capsys.readouterr().out


def test_no_recording_in_the_release_fails(freezer, monkeypatch, tmp_path, released):
    api = FakeApi(tmp_path, {"hf-new": "src-after-release"})
    with pytest.raises(SystemExit, match="no recording"):
        run(freezer, monkeypatch, api)
    assert api.created_tags == []


def test_the_tag_is_the_one_release_yml_names(freezer, tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "flip"\nversion = "0.13.0"\n')
    assert freezer.release_tag(pyproject) == "v0.13.0"


def test_contains_asks_git_about_this_repository(freezer):
    head = "HEAD"
    assert freezer.contains(head, head)
    assert not freezer.contains(head, "0" * 40)


class TestReleaseWorkflow:
    """release.yml runs the freeze before it pushes the git tag, and a failed freeze does not stop the release."""

    @pytest.fixture(scope="class")
    def jobs(self):
        return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())["jobs"]

    def test_the_release_waits_for_the_freeze_but_not_on_its_success(self, jobs):
        assert jobs["release"]["needs"] == "docs-gifs"
        assert "!cancelled()" in jobs["release"]["if"]

    def test_the_dataset_token_reaches_only_the_freeze_step(self, jobs):
        freeze = jobs["docs-gifs"]
        assert freeze["permissions"] == {"contents": "read"}
        holders = [step["name"] for job in jobs.values() for step in job["steps"] if "secrets.HF_TOKEN" in str(step)]
        assert holders == ["Tag the dataset"]
        tag_step = next(step for step in freeze["steps"] if step["name"] == "Tag the dataset")
        assert "freeze_docs_gifs.py" in tag_step["run"]
        assert freeze["steps"][0]["with"]["fetch-depth"] == 0
