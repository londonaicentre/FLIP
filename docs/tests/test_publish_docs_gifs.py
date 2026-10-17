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

"""publish_docs_gifs: the spec-derived file set, the manifest, the one-commit-one-tag contract and its refusals."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from huggingface_hub import CommitOperationAdd, CommitOperationDelete

SOURCE_COMMIT = "594524287f1826969c1a3cb250dd4fbeb2ac5fa3"  # pragma: allowlist secret (a git sha)
TAG = "20260907T122006Z-5945242"


class FakeApi:
    """Records what the publisher asks of the Hub; answers with fixed tags and files."""

    def __init__(
        self, tags: list[str] | None = None, files: list[str] | None = None, tag_error: Exception | None = None
    ):
        self.tags = tags or []
        self.files = files if files is not None else [".gitattributes"]
        self.tag_error = tag_error
        self.commits: list[dict] = []
        self.created_tags: list[dict] = []

    def list_repo_refs(self, repo, repo_type):
        return SimpleNamespace(tags=[SimpleNamespace(name=t) for t in self.tags])

    def list_repo_files(self, repo, repo_type):
        return list(self.files)

    def create_commit(self, repo, repo_type, operations, commit_message, commit_description):
        self.commits.append({"repo": repo, "operations": list(operations), "message": commit_message})
        return SimpleNamespace(oid=f"sha-{len(self.commits)}")

    def create_tag(self, repo, tag, revision, repo_type, tag_message):
        if self.tag_error is not None:
            raise self.tag_error
        self.created_tags.append({"tag": tag, "revision": revision})


@pytest.fixture(autouse=True)
def no_live_gifs(publisher, monkeypatch, tmp_path):
    """The repo's own live-recorded list stays out of these tests; TestLiveRecorded opts back in explicitly."""
    monkeypatch.setattr(publisher, "DEFAULT_LIVE_LIST", tmp_path / "no-live-list.txt")


def write(path: Path, data: bytes = b"GIF89a") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> SimpleNamespace:
    """A specs tree with two categories plus support/, and a matching GIF tree."""
    specs = tmp_path / "specs"
    gifs = tmp_path / "gifs"
    for name in ("admin/create-user", "flip/create-model", "flip/approve-project"):
        write(specs / f"{name}.spec.ts", b"describe()")
        write(gifs / f"{name}.gif", f"GIF89a {name}".encode())
    write(specs / "support/index.ts", b"export {}")
    write(specs / "support/demoCursor.ts", b"export {}")
    card = write(tmp_path / "card.md", b"---\nlicense: apache-2.0\n---\n")
    return SimpleNamespace(specs=specs, gifs=gifs, card=card)


def paths_of(ops) -> list[str]:
    return [op.path_in_repo for op in ops]


class TestFileSet:
    def test_specs_map_to_gifs_and_support_is_skipped(self, publisher, tree):
        assert publisher.expected_gifs(tree.specs) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
        ]

    def test_a_spec_at_the_wrong_depth_is_refused(self, publisher, tree):
        write(tree.specs / "flip/nested/x.spec.ts")
        with pytest.raises(SystemExit, match="<category>/<name>.spec.ts"):
            publisher.expected_gifs(tree.specs)

    def test_an_empty_tree_is_refused(self, publisher, tmp_path):
        with pytest.raises(SystemExit, match="no <category>/<name>.spec.ts"):
            publisher.expected_gifs(tmp_path)

    def test_a_spec_without_a_gif_is_refused_naming_it(self, publisher, tree):
        (tree.gifs / "flip/create-model.gif").unlink()
        with pytest.raises(SystemExit, match="missing: flip/create-model.gif"):
            publisher.build_manifest(
                tree.gifs,
                publisher.expected_gifs(tree.specs),
                version=TAG,
                source_commit=SOURCE_COMMIT,
                recorded_at="2026-09-07T12:20:06Z",
                workflow_run=None,
            )


class TestManifest:
    def test_content(self, publisher, tree):
        paths = publisher.expected_gifs(tree.specs)
        manifest = publisher.build_manifest(
            tree.gifs,
            paths,
            version=TAG,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run="https://github.com/o/r/actions/runs/1",
        )
        assert manifest["version"] == TAG
        assert manifest["source_commit"] == SOURCE_COMMIT
        assert manifest["recorded_at"] == "2026-09-07T12:20:06Z"
        assert manifest["workflow_run"] == "https://github.com/o/r/actions/runs/1"
        assert list(manifest["files"]) == paths  # sorted
        data = (tree.gifs / "flip/create-model.gif").read_bytes()
        assert manifest["files"]["flip/create-model.gif"] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    def test_bytes_are_canonical_json_with_a_trailing_newline(self, publisher):
        raw = publisher.manifest_bytes({"version": TAG, "files": {"b/x.gif": {}, "a/y.gif": {}}})
        assert raw.endswith(b"}\n")
        assert list(json.loads(raw)["files"]) == ["a/y.gif", "b/x.gif"]

    def test_tag_shape_and_timestamp(self, publisher):
        now = datetime(2026, 9, 18, 10, 15, 0, tzinfo=UTC)
        assert publisher.make_tag(SOURCE_COMMIT, now) == "20260918T101500Z-5945242"
        assert publisher.tag_timestamp(TAG) == "2026-09-07T12:20:06Z"
        assert publisher.tag_timestamp("v1") is None
        assert publisher.TAG_RE.fullmatch(TAG)
        assert not publisher.TAG_RE.fullmatch("20260907-5945242")


class TestOperations:
    def test_adds_every_gif_the_manifest_and_the_card(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=TAG,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        ops = publisher.build_operations(tree.gifs, manifest, [".gitattributes"], tree.card)
        assert paths_of(ops) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
            "manifest.json",
            "README.md",
        ]
        assert all(isinstance(op, CommitOperationAdd) for op in ops)
        manifest_op = ops[3]
        assert isinstance(manifest_op.path_or_fileobj, bytes)
        assert json.loads(manifest_op.path_or_fileobj)["version"] == TAG

    def test_a_removed_spec_deletes_its_dataset_gif_and_nothing_else(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=TAG,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        published = [".gitattributes", "README.md", "manifest.json", "admin/create-user.gif", "flip/retired.gif"]
        ops = publisher.build_operations(tree.gifs, manifest, published, None)
        deletes = [op for op in ops if isinstance(op, CommitOperationDelete)]
        assert paths_of(deletes) == ["flip/retired.gif"]
        assert "README.md" not in paths_of(ops)

    def test_a_stray_local_gif_is_ignored_with_a_warning(self, publisher, tree, capsys):
        write(tree.gifs / "flip/no-spec.gif")
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=TAG,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        ops = publisher.build_operations(tree.gifs, manifest, [], None)
        assert "flip/no-spec.gif" not in paths_of(ops)
        assert "no spec: flip/no-spec.gif" in capsys.readouterr().out

    def test_a_missing_card_is_refused(self, publisher, tree, tmp_path):
        manifest = {"version": TAG, "files": {}}
        with pytest.raises(SystemExit, match="missing card"):
            publisher.build_operations(tree.gifs, manifest, [], tmp_path / "absent.md")


class TestPublish:
    def ops(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=TAG,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        return publisher.build_operations(tree.gifs, manifest, [], tree.card)

    def test_an_existing_tag_is_refused_before_any_write(self, publisher, tree):
        api = FakeApi(tags=[TAG])
        with pytest.raises(SystemExit, match="never moved"):
            publisher.publish(api, TAG, self.ops(publisher, tree), "aicentreflip/docs-gifs", dry_run=False)
        assert api.commits == []

    def test_one_commit_then_the_tag_on_it(self, publisher, tree, capsys):
        api = FakeApi()
        oid = publisher.publish(api, TAG, self.ops(publisher, tree), "aicentreflip/docs-gifs", dry_run=False)
        assert oid == "sha-1"
        assert len(api.commits) == 1
        assert api.created_tags == [{"tag": TAG, "revision": "sha-1"}]
        out = capsys.readouterr().out
        assert f"https://huggingface.co/datasets/aicentreflip/docs-gifs/tree/{TAG}" in out
        assert "docs/.gifs_version" in out

    def test_a_tag_failure_after_the_commit_names_the_commit(self, publisher, tree):
        api = FakeApi(tag_error=RuntimeError("boom"))
        with pytest.raises(SystemExit, match="commit sha-1 landed .* tagging it .* failed"):
            publisher.publish(api, TAG, self.ops(publisher, tree), "aicentreflip/docs-gifs", dry_run=False)

    def test_dry_run_writes_nothing(self, publisher, tree, capsys):
        api = FakeApi()
        assert publisher.publish(api, TAG, self.ops(publisher, tree), "aicentreflip/docs-gifs", dry_run=True) is None
        assert api.commits == []
        assert api.created_tags == []
        assert "dry run" in capsys.readouterr().out


class TestCli:
    def test_end_to_end_with_a_fake_hub(self, publisher, tree, monkeypatch):
        api = FakeApi(files=[".gitattributes", "flip/retired.gif"])
        monkeypatch.setattr(publisher, "HfApi", lambda: api)
        rc = publisher.main(
            [
                "--source-commit",
                SOURCE_COMMIT,
                "--version",
                TAG,
                "--gifs-dir",
                str(tree.gifs),
                "--specs-dir",
                str(tree.specs),
                "--card",
                str(tree.card),
                "--repo",
                "aicentreflip/docs-gifs",
                "--workflow-run",
                "https://github.com/o/r/actions/runs/1",
            ]
        )
        assert rc == 0
        ops = api.commits[0]["operations"]
        assert paths_of(ops) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
            "manifest.json",
            "README.md",
            "flip/retired.gif",
        ]
        manifest = json.loads(ops[3].path_or_fileobj)
        assert manifest["workflow_run"] == "https://github.com/o/r/actions/runs/1"
        assert manifest["recorded_at"] == "2026-09-07T12:20:06Z"
        assert api.created_tags[0]["tag"] == TAG

    def test_the_tag_defaults_to_now_and_the_source_commit(self, publisher, tree, monkeypatch):
        api = FakeApi()
        monkeypatch.setattr(publisher, "HfApi", lambda: api)
        assert (
            publisher.main(
                [
                    "--source-commit",
                    SOURCE_COMMIT,
                    "--gifs-dir",
                    str(tree.gifs),
                    "--specs-dir",
                    str(tree.specs),
                    "--card",
                    str(tree.card),
                ]
            )
            == 0
        )
        tag = api.created_tags[0]["tag"]
        assert publisher.TAG_RE.fullmatch(tag)
        assert tag.endswith("-5945242")

    @pytest.mark.parametrize(
        ("version", "message"),
        [
            ("20260907-5945242", "YYYYMMDDTHHMMSSZ-<sha7>"),
            ("20260907T122006Z-0000000", "does not name --source-commit"),
        ],
    )
    def test_a_malformed_or_mismatched_version_is_refused(self, publisher, tree, monkeypatch, version, message):
        monkeypatch.setattr(publisher, "HfApi", lambda: FakeApi())
        with pytest.raises(SystemExit, match=message):
            publisher.main(
                [
                    "--source-commit",
                    SOURCE_COMMIT,
                    "--version",
                    version,
                    "--gifs-dir",
                    str(tree.gifs),
                    "--specs-dir",
                    str(tree.specs),
                    "--card",
                    str(tree.card),
                ]
            )

    def test_allow_any_tag_falls_back_to_now_for_recorded_at(self, publisher, tree, monkeypatch):
        api = FakeApi()
        monkeypatch.setattr(publisher, "HfApi", lambda: api)
        assert (
            publisher.main(
                [
                    "--source-commit",
                    SOURCE_COMMIT,
                    "--version",
                    "experiment-1",
                    "--allow-any-tag",
                    "--gifs-dir",
                    str(tree.gifs),
                    "--specs-dir",
                    str(tree.specs),
                    "--card",
                    str(tree.card),
                ]
            )
            == 0
        )
        manifest = json.loads(api.commits[0]["operations"][3].path_or_fileobj)
        assert manifest["version"] == "experiment-1"
        datetime.strptime(manifest["recorded_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_a_short_source_commit_is_refused(self, publisher, tree, monkeypatch):
        monkeypatch.setattr(publisher, "HfApi", lambda: FakeApi())
        with pytest.raises(SystemExit, match="full 40-hex"):
            publisher.main(["--source-commit", "5945242", "--gifs-dir", str(tree.gifs), "--specs-dir", str(tree.specs)])


class TestLiveRecorded:
    """GIFs recorded on a live trust: listed by hand, published with the Cypress set, never dropped by CI."""

    def test_listed_gifs_join_the_spec_set(self, publisher, tree, tmp_path):
        live = write(tmp_path / "live.txt", b"# comment\n\nmonailabel/monailabel-export.gif  # trailing note\n")
        assert publisher.expected_gifs(tree.specs, live) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
            "monailabel/monailabel-export.gif",
        ]

    def test_a_malformed_line_is_refused(self, publisher, tree, tmp_path):
        live = write(tmp_path / "live.txt", b"MonaiLabel/Export.GIF\n")
        with pytest.raises(SystemExit, match="is not <category>/<name>.gif"):
            publisher.expected_gifs(tree.specs, live)

    def test_a_gif_cannot_be_both_a_spec_and_a_live_recording(self, publisher, tree, tmp_path):
        live = write(tmp_path / "live.txt", b"flip/create-model.gif\n")
        with pytest.raises(SystemExit, match="both a Cypress spec and a live recording"):
            publisher.expected_gifs(tree.specs, live)

    def test_the_repo_list_is_well_formed_and_disjoint_from_the_specs(self, publisher):
        specs = publisher.DEFAULT_SPECS_DIR
        live = publisher.live_gifs(publisher.DOCS_DIR / "scripts" / "live-recorded-gifs.txt")
        assert live, "the repo's live-recorded list is empty"
        assert set(live) <= set(
            publisher.expected_gifs(specs, publisher.DOCS_DIR / "scripts" / "live-recorded-gifs.txt")
        )


class TestCarryForward:
    """A CI publish has only the Cypress GIFs; the live ones come from the pinned version, byte for byte."""

    GIF = "monailabel/monailabel-export.gif"

    @staticmethod
    def hub(tmp_path: Path, files: dict[str, bytes], manifest: dict | None = None):
        """A fake ``download(filename)`` serving ``files`` and a manifest recording their sha256."""
        root = tmp_path / "hub"
        for name, data in files.items():
            write(root / name, data)
        if manifest is None:
            manifest = {
                "files": {n: {"sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d)} for n, d in files.items()}
            }
        write(root / "manifest.json", json.dumps(manifest).encode())
        calls: list[str] = []

        def download(filename: str) -> str:
            calls.append(filename)
            return str(root / filename)

        return download, calls

    def test_a_missing_live_gif_is_copied_from_the_pinned_version(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {self.GIF: b"GIF89a live"})
        copied = publisher.carry_forward(tree.gifs, [self.GIF], "repo", TAG, download)
        assert copied == [self.GIF]
        assert (tree.gifs / self.GIF).read_bytes() == b"GIF89a live"

    def test_a_local_live_gif_wins_and_nothing_is_fetched(self, publisher, tree, tmp_path):
        write(tree.gifs / self.GIF, b"GIF89a re-recorded")
        download, calls = self.hub(tmp_path, {self.GIF: b"GIF89a old"})
        assert publisher.carry_forward(tree.gifs, [self.GIF], "repo", TAG, download) == []
        assert calls == []
        assert (tree.gifs / self.GIF).read_bytes() == b"GIF89a re-recorded"

    def test_bytes_that_do_not_match_the_manifest_are_refused(self, publisher, tree, tmp_path):
        manifest = {"files": {self.GIF: {"sha256": "0" * 64, "bytes": 1}}}
        download, _ = self.hub(tmp_path, {self.GIF: b"GIF89a tampered"}, manifest)
        with pytest.raises(SystemExit, match="does not match that version's manifest"):
            publisher.carry_forward(tree.gifs, [self.GIF], "repo", TAG, download)
        assert not (tree.gifs / self.GIF).exists()

    def test_a_gif_the_pinned_version_never_had_is_refused(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {})
        with pytest.raises(SystemExit, match="record it on a live trust first"):
            publisher.carry_forward(tree.gifs, [self.GIF], "repo", TAG, download)

    def test_no_pinned_version_is_refused(self, publisher, tree):
        with pytest.raises(SystemExit, match="no pinned version"):
            publisher.carry_forward(tree.gifs, [self.GIF], "repo", None, lambda f: f)
