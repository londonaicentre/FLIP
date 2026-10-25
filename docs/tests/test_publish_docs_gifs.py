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

"""publish_docs_gifs: the spec-derived file set, the manifest, the one-commit-per-recording contract, its refusals."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from huggingface_hub import CommitOperationAdd, CommitOperationDelete
from huggingface_hub.errors import EntryNotFoundError

SOURCE_COMMIT = "594524287f1826969c1a3cb250dd4fbeb2ac5fa3"  # pragma: allowlist secret (a git sha)
VERSION = "20260907T122006Z-5945242"
RUN = "https://github.com/o/r/actions/runs/1"


class FakeApi:
    """Records what the publisher asks of the Hub; answers with fixed branches and files per branch."""

    def __init__(self, files: dict[str, list[str]] | None = None):
        self.files = files if files is not None else {"main": [".gitattributes"]}
        self.commits: list[dict] = []
        self.created_branches: list[dict] = []
        self.listed: list[str] = []

    def list_repo_refs(self, repo, repo_type):
        return SimpleNamespace(branches=[SimpleNamespace(name=b) for b in self.files])

    def list_repo_files(self, repo, repo_type, revision):
        self.listed.append(revision)
        return list(self.files[revision])

    def create_branch(self, repo, branch, revision, repo_type):
        self.created_branches.append({"branch": branch, "from": revision})

    def create_commit(self, repo, repo_type, revision, operations, commit_message, commit_description):
        self.commits.append({"branch": revision, "operations": list(operations), "message": commit_message})
        return SimpleNamespace(oid=f"sha-{len(self.commits)}")


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
                version=VERSION,
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
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run="https://github.com/o/r/actions/runs/1",
        )
        assert manifest["version"] == VERSION
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
        raw = publisher.manifest_bytes({"version": VERSION, "files": {"b/x.gif": {}, "a/y.gif": {}}})
        assert raw.endswith(b"}\n")
        assert list(json.loads(raw)["files"]) == ["a/y.gif", "b/x.gif"]

    def test_version_shape_and_timestamp(self, publisher):
        now = datetime(2026, 9, 18, 10, 15, 0, tzinfo=UTC)
        assert publisher.make_version(SOURCE_COMMIT, now) == "20260918T101500Z-5945242"
        assert publisher.version_timestamp(VERSION) == "2026-09-07T12:20:06Z"
        assert publisher.version_timestamp("v1") is None
        assert publisher.VERSION_RE.fullmatch(VERSION)
        assert not publisher.VERSION_RE.fullmatch("20260907-5945242")


class TestOperations:
    def test_adds_every_gif_the_manifest_and_the_card(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=VERSION,
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
        assert json.loads(manifest_op.path_or_fileobj)["version"] == VERSION

    def test_a_removed_spec_deletes_its_dataset_gif_and_nothing_else(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=VERSION,
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
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        ops = publisher.build_operations(tree.gifs, manifest, [], None)
        assert "flip/no-spec.gif" not in paths_of(ops)
        assert "no spec: flip/no-spec.gif" in capsys.readouterr().out

    def test_a_missing_card_is_refused(self, publisher, tree, tmp_path):
        manifest = {"version": VERSION, "files": {}}
        with pytest.raises(SystemExit, match="missing card"):
            publisher.build_operations(tree.gifs, manifest, [], tmp_path / "absent.md")


class TestPublish:
    def ops(self, publisher, tree):
        manifest = publisher.build_manifest(
            tree.gifs,
            publisher.expected_gifs(tree.specs),
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            recorded_at="2026-09-07T12:20:06Z",
            workflow_run=None,
        )
        return publisher.build_operations(tree.gifs, manifest, [], tree.card)

    def publish(self, publisher, tree, api, branch="main", *, new_branch=False, dry_run=False):
        ops = self.ops(publisher, tree)
        return publisher.publish(
            api, VERSION, ops, "aicentreflip/docs-gifs", branch, new_branch=new_branch, dry_run=dry_run
        )

    def test_one_commit_on_the_branch_and_nothing_else(self, publisher, tree, capsys):
        api = FakeApi()
        assert self.publish(publisher, tree, api) == "sha-1"
        assert [commit["branch"] for commit in api.commits] == ["main"]
        assert api.created_branches == []
        assert "https://huggingface.co/datasets/aicentreflip/docs-gifs/tree/main" in capsys.readouterr().out

    def test_a_new_branch_is_cut_from_main_before_the_commit(self, publisher, tree):
        api = FakeApi()
        self.publish(publisher, tree, api, "pr-945", new_branch=True)
        assert api.created_branches == [{"branch": "pr-945", "from": "main"}]
        assert [commit["branch"] for commit in api.commits] == ["pr-945"]

    def test_dry_run_writes_nothing(self, publisher, tree, capsys):
        api = FakeApi()
        assert self.publish(publisher, tree, api, "pr-945", new_branch=True, dry_run=True) is None
        assert api.commits == []
        assert api.created_branches == []
        assert "dry run" in capsys.readouterr().out


class TestCli:
    def run(self, publisher, tree, monkeypatch, api, *extra: str) -> int:
        monkeypatch.setattr(publisher, "HfApi", lambda: api)
        args = ["--source-commit", SOURCE_COMMIT, "--gifs-dir", str(tree.gifs), "--specs-dir", str(tree.specs)]
        return publisher.main([*args, "--card", str(tree.card), *extra])

    def test_ci_publishes_main_end_to_end(self, publisher, tree, monkeypatch):
        api = FakeApi(files={"main": [".gitattributes", "flip/retired.gif"]})
        assert (
            self.run(publisher, tree, monkeypatch, api, "--branch", "main", "--version", VERSION, "--workflow-run", RUN)
            == 0
        )
        assert api.listed == ["main"]
        assert api.created_branches == []
        (commit,) = api.commits
        assert commit["branch"] == "main"
        assert paths_of(commit["operations"]) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
            "manifest.json",
            "README.md",
            "flip/retired.gif",
        ]
        manifest = json.loads(commit["operations"][3].path_or_fileobj)
        assert manifest["workflow_run"] == RUN
        assert manifest["recorded_at"] == "2026-09-07T12:20:06Z"

    def test_a_hand_recording_starts_its_pr_branch_from_main(self, publisher, tree, monkeypatch):
        api = FakeApi(files={"main": [".gitattributes", "flip/retired.gif"]})
        assert self.run(publisher, tree, monkeypatch, api, "--branch", "pr-945") == 0
        assert api.listed == ["main"]
        assert api.created_branches == [{"branch": "pr-945", "from": "main"}]
        assert api.commits[0]["branch"] == "pr-945"
        assert "flip/retired.gif" in paths_of(api.commits[0]["operations"])

    def test_a_second_hand_recording_reuses_its_branch(self, publisher, tree, monkeypatch):
        api = FakeApi(files={"main": [".gitattributes"], "pr-945": [".gitattributes", "flip/extra.gif"]})
        assert self.run(publisher, tree, monkeypatch, api, "--branch", "pr-945") == 0
        assert api.listed == ["pr-945"]
        assert api.created_branches == []
        assert "flip/extra.gif" in paths_of(api.commits[0]["operations"])

    def test_the_version_defaults_to_now_and_the_source_commit(self, publisher, tree, monkeypatch):
        api = FakeApi()
        assert self.run(publisher, tree, monkeypatch, api, "--branch", "pr-1") == 0
        version = json.loads(api.commits[0]["operations"][3].path_or_fileobj)["version"]
        assert publisher.VERSION_RE.fullmatch(version)
        assert version.endswith("-5945242")

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            (["--branch", "main"], "only the CI workflow publishes main"),
            (["--branch", "develop"], "main or pr-<N>"),
            (["--branch", "pr-1", "--version", "20260907-5945242"], "YYYYMMDDTHHMMSSZ-<sha7>"),
            (["--branch", "pr-1", "--version", "20260907T122006Z-0000000"], "does not name --source-commit"),
        ],
    )
    def test_refusals_write_nothing(self, publisher, tree, monkeypatch, extra, message):
        api = FakeApi()
        with pytest.raises(SystemExit, match=message):
            self.run(publisher, tree, monkeypatch, api, *extra)
        assert api.commits == []

    def test_allow_any_version_falls_back_to_now_for_recorded_at(self, publisher, tree, monkeypatch):
        api = FakeApi()
        assert (
            self.run(publisher, tree, monkeypatch, api, "--branch", "pr-1", "--version", "x-1", "--allow-any-version")
            == 0
        )
        manifest = json.loads(api.commits[0]["operations"][3].path_or_fileobj)
        assert manifest["version"] == "x-1"
        datetime.strptime(manifest["recorded_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_a_short_source_commit_is_refused(self, publisher, tree, monkeypatch):
        monkeypatch.setattr(publisher, "HfApi", lambda: FakeApi())
        with pytest.raises(SystemExit, match="full 40-hex"):
            publisher.main(["--branch", "pr-1", "--source-commit", "5945242", "--gifs-dir", str(tree.gifs)])


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
    """A CI publish has only the Cypress GIFs; the live ones come from the first dataset ref that has them."""

    GIF = "monailabel/monailabel-export.gif"

    @staticmethod
    def hub(tmp_path: Path, refs: dict[str, dict[str, bytes]], manifests: dict[str, dict] | None = None):
        """A fake ``download(filename, ref)`` serving each ref's files and a manifest recording their sha256.

        A ref missing from ``refs`` does not exist on the dataset.
        """
        root = tmp_path / "hub"
        for ref, files in refs.items():
            for name, data in files.items():
                write(root / ref / name, data)
            manifest = (manifests or {}).get(ref) or {
                "files": {n: {"sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d)} for n, d in files.items()}
            }
            write(root / ref / "manifest.json", json.dumps(manifest).encode())
        calls: list[tuple[str, str]] = []

        def download(filename: str, ref: str) -> str:
            calls.append((filename, ref))
            path = root / ref / filename
            if not path.is_file():
                raise EntryNotFoundError(f"{filename} at {ref}")
            return str(path)

        return download, calls

    def test_a_missing_live_gif_comes_from_the_first_ref_that_has_it(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {"pr-945": {self.GIF: b"GIF89a re-cut"}, "main": {self.GIF: b"GIF89a old"}})
        refs = ["pr-1", "pr-945", "main"]  # pr-1 was never published
        assert publisher.carry_forward(tree.gifs, [self.GIF], "repo", refs, download) == [self.GIF]
        assert (tree.gifs / self.GIF).read_bytes() == b"GIF89a re-cut"

    def test_main_is_the_fallback(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {"pr-945": {}, "main": {self.GIF: b"GIF89a live"}})
        publisher.carry_forward(tree.gifs, [self.GIF], "repo", ["pr-945", "main"], download)
        assert (tree.gifs / self.GIF).read_bytes() == b"GIF89a live"

    def test_a_local_live_gif_wins_and_nothing_is_fetched(self, publisher, tree, tmp_path):
        write(tree.gifs / self.GIF, b"GIF89a re-recorded")
        download, calls = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a old"}})
        assert publisher.carry_forward(tree.gifs, [self.GIF], "repo", ["main"], download) == []
        assert calls == []
        assert (tree.gifs / self.GIF).read_bytes() == b"GIF89a re-recorded"

    def test_bytes_that_do_not_match_the_manifest_are_refused(self, publisher, tree, tmp_path):
        manifests = {"main": {"files": {self.GIF: {"sha256": "0" * 64, "bytes": 1}}}}
        download, _ = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a tampered"}}, manifests)
        with pytest.raises(SystemExit, match="does not match that ref's manifest"):
            publisher.carry_forward(tree.gifs, [self.GIF], "repo", ["main"], download)
        assert not (tree.gifs / self.GIF).exists()

    def test_a_gif_no_ref_has_is_refused(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {"main": {}})
        with pytest.raises(SystemExit, match="on none of pr-945, main .* record it on a live trust first"):
            publisher.carry_forward(tree.gifs, [self.GIF], "repo", ["pr-945", "main"], download)

    def test_the_cli_tries_carry_from_then_the_branch_then_main(self, publisher, tree, tmp_path, monkeypatch):
        live = write(tmp_path / "live.txt", f"{self.GIF}\n".encode())
        seen: list[list[str]] = []
        monkeypatch.setattr(publisher, "carry_forward", lambda gifs, paths, repo, refs, dl: seen.append(refs))
        write(tree.gifs / self.GIF)  # carry_forward is stubbed, so the GIF must already be there
        monkeypatch.setattr(publisher, "HfApi", lambda: FakeApi())
        args = ["--source-commit", SOURCE_COMMIT, "--gifs-dir", str(tree.gifs), "--specs-dir", str(tree.specs)]
        publisher.main([*args, "--card", str(tree.card), "--live-list", str(live), "--branch", "pr-7"])
        publisher.main(
            [
                *args,
                "--card",
                str(tree.card),
                "--live-list",
                str(live),
                "--branch",
                "main",
                "--workflow-run",
                RUN,
                "--carry-from",
                "pr-945",
            ]
        )
        assert seen == [["pr-7", "main"], ["pr-945", "main"]]
