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

"""fetch_docs_gifs: which revision a build gets, the manifest contract, verification, retries, and the CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

TAG = "20260907T122006Z-5945242"
COMMIT = "28a0f427e623cde17bf35690ffdc2a0d7e79a9cf"  # pragma: allowlist secret (a dataset commit sha)
REPO = "aicentreflip/docs-gifs"
BASE = f"https://huggingface.co/datasets/{REPO}/resolve"
API = f"https://huggingface.co/api/datasets/{REPO}/revision"


class FakeResponse:
    """Just enough of requests.Response: status, headers, .content, streaming .iter_content, .close()."""

    def __init__(
        self, status_code: int = 200, body: bytes = b"", headers: dict | None = None, break_after: int | None = None
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._break_after = break_after
        self.closed = False

    @property
    def content(self) -> bytes:
        return self._body

    def iter_content(self, chunk_size: int):
        sent = 0
        for start in range(0, len(self._body), chunk_size):
            chunk = self._body[start : start + chunk_size]
            sent += len(chunk)
            yield chunk
            if self._break_after is not None and sent >= self._break_after:
                raise requests.exceptions.ChunkedEncodingError("connection broken mid-body")

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Serves a queue of responses (or exceptions) per URL and records every GET."""

    def __init__(self, routes: dict[str, list] | None = None):
        self.routes = {url: list(queue) for url, queue in (routes or {}).items()}
        self.calls: list[str] = []

    def get(self, url: str, *, stream: bool = False, timeout=None):
        self.calls.append(url)
        queue = self.routes.get(url)
        if not queue:
            raise AssertionError(f"unexpected GET {url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_for(files: dict[str, bytes], version: str = TAG) -> dict:
    return {
        "version": version,
        "source_commit": "594524287f1826969c1a3cb250dd4fbeb2ac5fa3",  # pragma: allowlist secret (a git sha)
        "recorded_at": "2026-09-07T12:20:06Z",
        "workflow_run": None,
        "files": {path: {"sha256": sha(data), "bytes": len(data)} for path, data in files.items()},
    }


def revision_route(revision: str = TAG, commit: str = COMMIT) -> dict[str, list]:
    return {f"{API}/{revision}": [FakeResponse(body=json.dumps({"id": REPO, "sha": commit}).encode())]}


def routes_for(files: dict[str, bytes], revision: str = TAG, version: str = TAG, commit: str = COMMIT) -> dict:
    manifest = json.dumps(manifest_for(files, version)).encode()
    routes = {**revision_route(revision, commit), f"{BASE}/{commit}/manifest.json": [FakeResponse(body=manifest)]}
    for path, data in files.items():
        routes[f"{BASE}/{commit}/{path}"] = [FakeResponse(body=data)]
    return routes


FILES = {"admin/create-user.gif": b"GIF89a-admin" * 1000, "flip/create-model.gif": b"GIF89a-flip" * 2000}
GOOD_SHA = "a" * 64


def manifest_with(path: str, sha256, size) -> bytes:
    """A one-file manifest whose entry may be deliberately malformed."""
    return json.dumps({"version": "t", "files": {path: {"sha256": sha256, "bytes": size}}}).encode()


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "gifs"


def run_fetch(fetcher, dest: Path, session: FakeSession, revisions: tuple[str, ...] = (TAG,), log=None):
    sleeps: list[float] = []
    report = fetcher.fetch(REPO, revisions, dest, session=session, log=log or (lambda _m: None), sleep=sleeps.append)
    return report, sleeps


class TestColdFetch:
    def test_resolves_the_revision_then_reads_everything_at_its_commit(self, fetcher, dest):
        session = FakeSession(routes_for(FILES))
        report, _ = run_fetch(fetcher, dest, session)
        assert session.calls[:2] == [f"{API}/{TAG}", f"{BASE}/{COMMIT}/manifest.json"]
        assert sorted(session.calls[2:]) == sorted(f"{BASE}/{COMMIT}/{p}" for p in FILES)
        for path, data in FILES.items():
            assert (dest / path).read_bytes() == data
        assert not list(dest.rglob("*.part"))
        assert json.loads((dest / "manifest.json").read_text())["version"] == TAG
        assert sorted(report.fetched) == sorted(FILES)
        assert report.verified == []
        assert (report.revision, report.commit, report.version) == (TAG, COMMIT, TAG)

    def test_sha_mismatch_fails_naming_the_url_and_leaves_no_file(self, fetcher, dest):
        routes = routes_for(FILES)
        bad_url = f"{BASE}/{COMMIT}/admin/create-user.gif"
        routes[bad_url] = [FakeResponse(body=b"x" * len(FILES["admin/create-user.gif"]))]
        with pytest.raises(fetcher.FetchError, match="create-user.gif.*sha256"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "admin/create-user.gif").exists()
        assert not list(dest.rglob("*.part"))
        assert not (dest / "manifest.json").exists()

    def test_size_mismatch_fails(self, fetcher, dest):
        routes = routes_for(FILES)
        routes[f"{BASE}/{COMMIT}/flip/create-model.gif"] = [FakeResponse(body=FILES["flip/create-model.gif"] + b"!")]
        with pytest.raises(fetcher.FetchError, match="create-model.gif.*bytes"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "flip/create-model.gif").exists()

    def test_a_size_mismatch_with_the_right_sha_is_still_refused(self, fetcher, dest):
        routes = routes_for(FILES)
        manifest = manifest_for(FILES)
        manifest["files"]["flip/create-model.gif"]["bytes"] += 1
        routes[f"{BASE}/{COMMIT}/manifest.json"] = [FakeResponse(body=json.dumps(manifest).encode())]
        with pytest.raises(fetcher.FetchError, match="create-model.gif.*bytes"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "flip/create-model.gif").exists()

    def test_a_missing_manifest_fails_without_retry(self, fetcher, dest):
        url = f"{BASE}/{COMMIT}/manifest.json"
        session = FakeSession({**revision_route(), url: [FakeResponse(status_code=404)]})
        with pytest.raises(fetcher.FetchError, match="HTTP 404.*revision"):
            run_fetch(fetcher, dest, session)
        assert session.calls == [f"{API}/{TAG}", url]


class TestRevisions:
    def test_the_first_revision_that_exists_wins_and_the_skip_is_logged(self, fetcher, dest):
        routes = {f"{API}/pr-945": [FakeResponse(status_code=404)], **routes_for(FILES, revision="main")}
        lines: list[str] = []
        report, _ = run_fetch(fetcher, dest, FakeSession(routes), revisions=("pr-945", "main"), log=lines.append)
        assert report.revision == "main"
        assert f"no pr-945 on {REPO}" in lines

    def test_none_existing_fails_naming_them_all(self, fetcher, dest):
        routes = {f"{API}/v9.9.9": [FakeResponse(status_code=404)], f"{API}/main": [FakeResponse(status_code=404)]}
        with pytest.raises(fetcher.FetchError, match="none of v9.9.9, main exists"):
            run_fetch(fetcher, dest, FakeSession(routes), revisions=("v9.9.9", "main"))

    @pytest.mark.parametrize(
        ("environ", "expected"),
        [
            ({}, ["main"]),
            ({"READTHEDOCS_VERSION_TYPE": "branch", "READTHEDOCS_VERSION_NAME": "latest"}, ["main"]),
            ({"READTHEDOCS_VERSION_TYPE": "external", "READTHEDOCS_VERSION_NAME": "945"}, ["pr-945", "main"]),
            ({"READTHEDOCS_VERSION_TYPE": "tag", "READTHEDOCS_VERSION_NAME": "v0.13.0"}, ["v0.13.0", "main"]),
            ({"READTHEDOCS_VERSION_TYPE": "tag", "READTHEDOCS_VERSION_NAME": "flip-utils-v0.13.0"}, ["main"]),
            ({"READTHEDOCS_VERSION_TYPE": "branch", "READTHEDOCS_VERSION_NAME": "main"}, ["v4.5.6", "main"]),
            ({"READTHEDOCS_VERSION_TYPE": "tag", "READTHEDOCS_VERSION_NAME": "stable"}, ["v4.5.6", "main"]),
            ({"FLIP_DOCS_GIFS_REVISION": TAG, "READTHEDOCS_VERSION_TYPE": "external"}, [TAG]),
        ],
    )
    def test_what_each_build_tries(self, fetcher, monkeypatch, environ, expected):
        monkeypatch.setattr(fetcher, "project_version", lambda: "4.5.6")
        assert fetcher.revisions_to_try(environ=environ) == expected

    def test_an_explicit_revision_beats_the_environment(self, fetcher):
        assert fetcher.revisions_to_try("abc1234", environ={"FLIP_DOCS_GIFS_REVISION": TAG}) == ["abc1234"]

    def test_the_project_version_is_read_from_pyproject(self, fetcher, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "flip"\nversion = "0.13.0"\n')
        assert fetcher.project_version(pyproject) == "0.13.0"


class TestIncremental:
    def test_a_second_build_downloads_nothing(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        routes = routes_for(FILES)
        for path in FILES:
            del routes[f"{BASE}/{COMMIT}/{path}"]  # must not be requested
        report, _ = run_fetch(fetcher, dest, FakeSession(routes))
        assert report.fetched == []
        assert sorted(report.verified) == sorted(FILES)

    def test_a_file_whose_local_bytes_differ_is_refetched_alone(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        (dest / "admin/create-user.gif").write_bytes(b"locally re-recorded")
        routes = routes_for(FILES)
        del routes[f"{BASE}/{COMMIT}/flip/create-model.gif"]
        report, _ = run_fetch(fetcher, dest, FakeSession(routes))
        assert report.fetched == ["admin/create-user.gif"]
        assert (dest / "admin/create-user.gif").read_bytes() == FILES["admin/create-user.gif"]

    def test_a_same_length_local_file_with_different_bytes_is_refetched(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        original = FILES["flip/create-model.gif"]
        (dest / "flip/create-model.gif").write_bytes(bytes(reversed(original)))
        report, _ = run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        assert report.fetched == ["flip/create-model.gif"]
        assert (dest / "flip/create-model.gif").read_bytes() == original

    def test_a_new_recording_refetches_only_the_files_whose_hash_changed(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        new_version, new_commit = "20260918T101500Z-946490c", "b" * 40
        changed = {**FILES, "flip/create-model.gif": b"GIF89a-flip-v2" * 2000}
        routes = routes_for(changed, revision="main", version=new_version, commit=new_commit)
        del routes[f"{BASE}/{new_commit}/admin/create-user.gif"]  # must not be requested
        report, _ = run_fetch(fetcher, dest, FakeSession(routes), revisions=("main",))
        assert report.fetched == ["flip/create-model.gif"]
        assert report.verified == ["admin/create-user.gif"]
        assert json.loads((dest / "manifest.json").read_text())["version"] == new_version


class TestRetries:
    def test_429_honours_retry_after_and_succeeds_on_the_third_attempt(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{BASE}/{COMMIT}/manifest.json"
        good = routes[url][0]
        routes[url] = [FakeResponse(status_code=429, headers={"Retry-After": "7"}), FakeResponse(status_code=503), good]
        session = FakeSession(routes)
        _, sleeps = run_fetch(fetcher, dest, session)
        assert session.calls[1:4] == [url, url, url]
        assert sleeps == [7.0, 2.0]

    def test_retry_after_is_capped(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{API}/{TAG}"
        good = routes[url][0]
        routes[url] = [FakeResponse(status_code=429, headers={"Retry-After": "999"}), good]
        _, sleeps = run_fetch(fetcher, dest, FakeSession(routes))
        assert sleeps == [float(fetcher.MAX_RETRY_AFTER_SECONDS)]

    def test_connection_errors_are_retried_then_give_up(self, fetcher, dest):
        url = f"{API}/{TAG}"
        session = FakeSession({url: [requests.ConnectionError("refused")] * fetcher.ATTEMPTS})
        with pytest.raises(fetcher.FetchError, match=f"giving up after {fetcher.ATTEMPTS} attempts"):
            run_fetch(fetcher, dest, session)
        assert session.calls == [url] * fetcher.ATTEMPTS

    def test_a_mid_transfer_failure_is_retried(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{BASE}/{COMMIT}/flip/create-model.gif"
        data = FILES["flip/create-model.gif"]
        routes[url] = [FakeResponse(body=data, break_after=1), FakeResponse(body=data)]
        session = FakeSession(routes)
        report, sleeps = run_fetch(fetcher, dest, session)
        assert session.calls.count(url) == 2
        assert sleeps == [1.0]
        assert (dest / "flip/create-model.gif").read_bytes() == data
        assert not list(dest.rglob("*.part"))
        assert "flip/create-model.gif" in report.fetched


class TestManifestContract:
    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (b"{not json", "not valid JSON"),
            (b'{"version": 1, "files": {}}', "string 'version'"),
            (b'{"version": "t", "files": []}', "object 'files'"),
            (manifest_with("../x.gif", GOOD_SHA, 1), "not <category>/<name>.gif"),
            (manifest_with("flip/x.png", GOOD_SHA, 1), "not <category>/<name>.gif"),
            (manifest_with("flip/x.gif", "abc", 1), "64-hex"),
            (manifest_with("flip/x.gif", GOOD_SHA, -1), "non-negative"),
            (manifest_with("flip/x.gif", GOOD_SHA, True), "non-negative"),
        ],
    )
    def test_malformed_manifests_are_refused(self, fetcher, raw, message):
        with pytest.raises(fetcher.FetchError, match=message):
            fetcher.parse_manifest(raw, "https://example/manifest.json")

    @pytest.mark.parametrize(
        "path",
        ["../flip/x.gif", "/etc/flip/x.gif", "flip/x.gif/../../../evil.gif", "flip/x.gif\n", "flip/a/x.gif", ".gif"],
    )
    def test_a_path_that_merely_contains_a_valid_shape_is_refused(self, fetcher, path):
        with pytest.raises(fetcher.FetchError, match="not <category>/<name>.gif"):
            fetcher.parse_manifest(manifest_with(path, GOOD_SHA, 1), "https://example/manifest.json")

    def test_a_manifest_can_never_direct_a_write_outside_the_destination(self, fetcher, dest, tmp_path):
        escaped = "../escaped/flip/x.gif"
        data = b"GIF89a"
        routes = {
            **revision_route(),
            f"{BASE}/{COMMIT}/manifest.json": [FakeResponse(body=manifest_with(escaped, sha(data), len(data)))],
            f"{BASE}/{COMMIT}/{escaped}": [FakeResponse(body=data)],
        }
        with pytest.raises(fetcher.FetchError):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert [p.name for p in tmp_path.iterdir() if p.name != dest.name] == []
        assert not dest.exists() or not list(dest.rglob("*"))

    def test_a_valid_manifest_round_trips(self, fetcher):
        manifest = manifest_for(FILES)
        assert fetcher.parse_manifest(json.dumps(manifest).encode(), "m") == manifest


class TestRepo:
    def test_repo_from_env(self, fetcher, monkeypatch):
        monkeypatch.delenv(fetcher.REPO_ENV, raising=False)
        assert fetcher.repo_from_env() == "aicentreflip/docs-gifs"
        monkeypatch.setenv(fetcher.REPO_ENV, "someone/fork")
        assert fetcher.repo_from_env() == "someone/fork"

    def test_the_default_destination_is_the_gitignored_generated_tree(self, fetcher):
        assert fetcher.DEFAULT_DEST == fetcher.DOCS_DIR / "source" / "assets" / "generated" / "gifs"


class TestCli:
    @pytest.fixture(autouse=True)
    def clean_env(self, fetcher, monkeypatch):
        for name in (fetcher.REVISION_ENV, fetcher.REPO_ENV, "READTHEDOCS_VERSION_TYPE", "READTHEDOCS_VERSION_NAME"):
            monkeypatch.delenv(name, raising=False)

    def test_a_contributor_build_fetches_main(self, fetcher, dest, monkeypatch, capsys):
        monkeypatch.setattr(fetcher.requests, "Session", lambda: FakeSession(routes_for(FILES, revision="main")))
        assert fetcher.main(["--dest", str(dest)]) == 0
        assert "@main" in capsys.readouterr().out
        assert (dest / "manifest.json").exists()

    def test_failure_exits_one_naming_the_revision(self, fetcher, dest, monkeypatch, capsys):
        routes = {f"{API}/{TAG}": [FakeResponse(status_code=404)]}
        monkeypatch.setattr(fetcher.requests, "Session", lambda: FakeSession(routes))
        assert fetcher.main(["--dest", str(dest), "--revision", TAG]) == 1
        assert f"none of {TAG} exists" in capsys.readouterr().err
