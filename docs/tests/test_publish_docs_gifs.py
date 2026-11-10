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
import requests
from huggingface_hub import CommitOperationAdd, CommitOperationDelete
from huggingface_hub.errors import LocalEntryNotFoundError, RemoteEntryNotFoundError, RevisionNotFoundError

PIN = "a" * 64
SOURCE_COMMIT = "594524287f1826969c1a3cb250dd4fbeb2ac5fa3"  # pragma: allowlist secret (a git sha)
VERSION = "20260907T122006Z-5945242"
RUN = "https://github.com/o/r/actions/runs/1"


class FakeApi:
    """Records what the publisher asks of the Hub; answers with fixed branches and files per branch."""

    def __init__(self, files: dict[str, list[str]] | None = None, tags: tuple[str, ...] = ()):
        self.files = files if files is not None else {"main": [".gitattributes"]}
        self.tags = tags
        self.commits: list[dict] = []
        self.created_branches: list[dict] = []
        self.listed: list[str] = []

    def list_repo_refs(self, repo, repo_type):
        return SimpleNamespace(
            branches=[SimpleNamespace(name=b) for b in self.files], tags=[SimpleNamespace(name=t) for t in self.tags]
        )

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
    monkeypatch.setattr(publisher, "DEFAULT_LIVE_LIST", write(tmp_path / "empty-live-list.txt", b""))


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
        live = write(tmp_path / "live.txt", f"# comment\n\nmonailabel/monailabel-export.gif {PIN}  # note\n".encode())
        assert publisher.expected_gifs(tree.specs, live) == [
            "admin/create-user.gif",
            "flip/approve-project.gif",
            "flip/create-model.gif",
            "monailabel/monailabel-export.gif",
        ]

    @pytest.mark.parametrize(
        "line",
        [
            f"MonaiLabel/Export.GIF {PIN}",
            "monailabel/monailabel-export.gif",
            "monailabel/monailabel-export.gif abc",
            f"monailabel/monailabel-export.gif {PIN} {PIN}",
        ],
    )
    def test_a_malformed_or_unpinned_line_is_refused(self, publisher, tree, tmp_path, line):
        live = write(tmp_path / "live.txt", f"{line}\n".encode())
        with pytest.raises(SystemExit, match="is not <category>/<name>.gif <sha256>"):
            publisher.expected_gifs(tree.specs, live)

    def test_a_path_listed_twice_is_refused(self, publisher, tree, tmp_path):
        live = write(tmp_path / "live.txt", f"m/a.gif {PIN}\nm/a.gif {'b' * 64}\n".encode())
        with pytest.raises(SystemExit, match="m/a.gif is listed twice"):
            publisher.expected_gifs(tree.specs, live)

    def test_a_missing_list_is_refused_not_read_as_empty(self, publisher, tree, tmp_path):
        """Read as empty, a mistyped --live-list would delete every live GIF from the branch."""
        with pytest.raises(SystemExit, match="no live-recorded list at"):
            publisher.expected_gifs(tree.specs, tmp_path / "typo.txt")

    def test_a_gif_cannot_be_both_a_spec_and_a_live_recording(self, publisher, tree, tmp_path):
        live = write(tmp_path / "live.txt", f"flip/create-model.gif {PIN}\n".encode())
        with pytest.raises(SystemExit, match="both a Cypress spec and a live recording"):
            publisher.expected_gifs(tree.specs, live)

    def test_the_repo_list_is_well_formed_and_disjoint_from_the_specs(self, publisher):
        specs = publisher.DEFAULT_SPECS_DIR
        live = publisher.live_gifs(publisher.DOCS_DIR / "scripts" / "live-recorded-gifs.txt")
        assert live, "the repo's live-recorded list is empty"
        assert set(live) <= set(
            publisher.expected_gifs(specs, publisher.DOCS_DIR / "scripts" / "live-recorded-gifs.txt")
        )


def not_found() -> requests.Response:
    response = requests.Response()
    response.status_code = 404
    return response


class TestCarryForward:
    """A CI publish has only the Cypress GIFs; the live ones are the pinned bytes, from any dataset ref holding them."""

    GIF = "monailabel/monailabel-export.gif"
    RECUT = b"GIF89a re-cut"
    LIVE = {GIF: hashlib.sha256(RECUT).hexdigest()}

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
            if not (root / ref).is_dir():
                raise RevisionNotFoundError(ref, response=not_found())
            if not (root / ref / filename).is_file():
                raise RemoteEntryNotFoundError(f"{filename} at {ref}", response=not_found())
            return str(root / ref / filename)

        return download, calls

    def test_the_pinned_bytes_come_from_the_first_ref_holding_them(self, publisher, tree, tmp_path):
        """main still has the old cut; the merged PR's branch has the pinned one (its own publish never ran)."""
        download, _ = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a old"}, "pr-945": {self.GIF: self.RECUT}})
        refs = ["main", "pr-1", "pr-945"]  # pr-1 was never published
        assert publisher.carry_forward(tree.gifs, self.LIVE, "repo", refs, download) == [self.GIF]
        assert (tree.gifs / self.GIF).read_bytes() == self.RECUT

    def test_every_live_gif_is_carried_not_just_the_first(self, publisher, tree, tmp_path):
        other = "monailabel/monailabel-enable.gif"
        write(tree.gifs / self.GIF, self.RECUT)
        download, _ = self.hub(tmp_path, {"main": {other: b"GIF89a enable"}})
        live = {**self.LIVE, other: hashlib.sha256(b"GIF89a enable").hexdigest()}
        assert publisher.carry_forward(tree.gifs, live, "repo", ["main"], download) == [other]

    def test_every_local_live_gif_is_checked_not_just_the_first(self, publisher, tree, tmp_path):
        other = "monailabel/monailabel-enable.gif"
        write(tree.gifs / self.GIF, self.RECUT)
        write(tree.gifs / other, b"GIF89a unpinned")
        download, _ = self.hub(tmp_path, {"main": {}})
        live = {**self.LIVE, other: PIN}
        with pytest.raises(SystemExit, match=f"{other} is not the pinned recording"):
            publisher.carry_forward(tree.gifs, live, "repo", ["main"], download)

    def test_a_network_error_is_not_read_as_an_absent_ref(self, publisher, tree, tmp_path):
        """Read as absent, a Hub blip would end in "publish it to the pr-<N>", sending the operator to re-record."""

        def download(filename: str, ref: str) -> str:
            raise LocalEntryNotFoundError("connection error")

        with pytest.raises(LocalEntryNotFoundError):
            publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main"], download)

    def test_main_serves_once_it_holds_them(self, publisher, tree, tmp_path):
        download, calls = self.hub(tmp_path, {"main": {self.GIF: self.RECUT}, "pr-945": {self.GIF: self.RECUT}})
        publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main", "pr-945"], download)
        assert {ref for _, ref in calls} == {"main"}

    def test_a_local_pinned_gif_wins_and_nothing_is_fetched(self, publisher, tree, tmp_path):
        write(tree.gifs / self.GIF, self.RECUT)
        download, calls = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a old"}})
        assert publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main"], download) == []
        assert calls == []

    def test_a_local_gif_that_is_not_pinned_is_refused_with_its_line(self, publisher, tree, tmp_path):
        write(tree.gifs / self.GIF, b"GIF89a another cut")
        download, _ = self.hub(tmp_path, {"main": {}})
        line = f"{self.GIF} {hashlib.sha256(b'GIF89a another cut').hexdigest()}"
        with pytest.raises(SystemExit, match=f"Re-cut it\\? Pin it: {line} — otherwise merge develop"):
            publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main"], download)

    def test_bytes_that_are_not_the_pinned_ones_are_refused(self, publisher, tree, tmp_path):
        """A ref whose manifest claims the pin but serves other bytes (edited after review)."""
        manifests = {"main": {"files": {self.GIF: {"sha256": self.LIVE[self.GIF], "bytes": 1}}}}
        download, _ = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a tampered"}}, manifests)
        with pytest.raises(SystemExit, match="is not the pinned recording"):
            publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main"], download)
        assert not (tree.gifs / self.GIF).exists()

    def test_pinned_bytes_no_ref_holds_are_refused(self, publisher, tree, tmp_path):
        download, _ = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a old"}})
        with pytest.raises(SystemExit, match="is on none of main, pr-945 .* publish it to the pr-<N>"):
            publisher.carry_forward(tree.gifs, self.LIVE, "repo", ["main", "pr-945"], download)

    def test_the_cli_tries_the_branch_main_every_pr_newest_first_then_tags(
        self, publisher, tree, tmp_path, monkeypatch
    ):
        live = write(tmp_path / "live.txt", f"{self.GIF} {PIN}\n".encode())
        seen: list[list[str]] = []
        monkeypatch.setattr(publisher, "carry_forward", lambda gifs, pins, repo, refs, dl: seen.append(refs))
        write(tree.gifs / self.GIF)  # carry_forward is stubbed, so the GIF must already be there
        branches = {b: [".gitattributes"] for b in ("main", "pr-945", "pr-1772", "pr-99", "scratch")}
        monkeypatch.setattr(publisher, "HfApi", lambda: FakeApi(files=dict(branches), tags=("v0.13.0",)))
        args = ["--source-commit", SOURCE_COMMIT, "--gifs-dir", str(tree.gifs), "--specs-dir", str(tree.specs)]
        args += ["--card", str(tree.card), "--live-list", str(live)]
        publisher.main([*args, "--branch", "pr-7"])
        publisher.main([*args, "--branch", "main", "--workflow-run", RUN])
        prs_then_tags = ["pr-1772", "pr-945", "pr-99", "v0.13.0"]
        assert seen == [["pr-7", "main", *prs_then_tags], ["main", *prs_then_tags]]

    def test_the_ci_publish_carries_the_pinned_gif_end_to_end(self, publisher, tree, tmp_path, monkeypatch):
        """The merged PR's own publish never ran: its pinned GIF is only on its pr-<N>, and still reaches main."""
        download, calls = self.hub(tmp_path, {"main": {self.GIF: b"GIF89a old"}, "pr-945": {self.GIF: self.RECUT}})
        monkeypatch.setattr(
            publisher, "hf_hub_download", lambda repo, name, repo_type, revision: download(name, revision)
        )
        api = FakeApi(files={"main": [".gitattributes"], "pr-945": [".gitattributes"]})
        monkeypatch.setattr(publisher, "HfApi", lambda: api)
        live = write(tmp_path / "live.txt", f"{self.GIF} {self.LIVE[self.GIF]}\n".encode())
        args = ["--source-commit", SOURCE_COMMIT, "--gifs-dir", str(tree.gifs), "--specs-dir", str(tree.specs)]
        publisher.main(
            [*args, "--card", str(tree.card), "--live-list", str(live), "--branch", "main", "--workflow-run", RUN]
        )
        assert (self.GIF, "pr-945") in calls
        assert self.GIF in paths_of(api.commits[0]["operations"])
        assert (tree.gifs / self.GIF).read_bytes() == self.RECUT
