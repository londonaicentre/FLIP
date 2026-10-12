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

"""fetch_docs_gifs: the manifest contract, the tag-only fast path, verification, retries, and the CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

TAG = "20260907T122006Z-5945242"
REPO = "aicentreflip/docs-gifs"
BASE = f"https://huggingface.co/datasets/{REPO}/resolve"


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


def routes_for(files: dict[str, bytes], revision: str = TAG, version: str = TAG) -> dict[str, list]:
    manifest = json.dumps(manifest_for(files, version)).encode()
    routes = {f"{BASE}/{revision}/manifest.json": [FakeResponse(body=manifest)]}
    for path, data in files.items():
        routes[f"{BASE}/{revision}/{path}"] = [FakeResponse(body=data)]
    return routes


FILES = {"admin/create-user.gif": b"GIF89a-admin" * 1000, "flip/create-model.gif": b"GIF89a-flip" * 2000}
GOOD_SHA = "a" * 64


def manifest_with(path: str, sha256, size) -> bytes:
    """A one-file manifest whose entry may be deliberately malformed."""
    return json.dumps({"version": "t", "files": {path: {"sha256": sha256, "bytes": size}}}).encode()


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "gifs"


def run_fetch(fetcher, dest: Path, session: FakeSession, revision: str = TAG):
    sleeps: list[float] = []
    report = fetcher.fetch(REPO, revision, dest, session=session, log=lambda _m: None, sleep=sleeps.append)
    return report, sleeps


class TestColdFetch:
    def test_downloads_manifest_then_files_and_writes_manifest_last(self, fetcher, dest):
        session = FakeSession(routes_for(FILES))
        report, _ = run_fetch(fetcher, dest, session)
        assert session.calls[0] == f"{BASE}/{TAG}/manifest.json"
        assert sorted(session.calls[1:]) == sorted(f"{BASE}/{TAG}/{p}" for p in FILES)
        for path, data in FILES.items():
            assert (dest / path).read_bytes() == data
        assert not list(dest.rglob("*.part"))
        assert json.loads((dest / "manifest.json").read_text())["version"] == TAG
        assert sorted(report.fetched) == sorted(FILES)
        assert report.verified == []
        assert report.version == TAG

    def test_sha_mismatch_fails_naming_the_url_and_leaves_no_file(self, fetcher, dest):
        routes = routes_for(FILES)
        bad_url = f"{BASE}/{TAG}/admin/create-user.gif"
        routes[bad_url] = [FakeResponse(body=b"x" * len(FILES["admin/create-user.gif"]))]
        with pytest.raises(fetcher.FetchError, match="create-user.gif.*sha256"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "admin/create-user.gif").exists()
        assert not list(dest.rglob("*.part"))
        assert not (dest / "manifest.json").exists()

    def test_size_mismatch_fails(self, fetcher, dest):
        routes = routes_for(FILES)
        routes[f"{BASE}/{TAG}/flip/create-model.gif"] = [FakeResponse(body=FILES["flip/create-model.gif"] + b"!")]
        with pytest.raises(fetcher.FetchError, match="create-model.gif.*bytes"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "flip/create-model.gif").exists()

    def test_a_size_mismatch_with_the_right_sha_is_still_refused(self, fetcher, dest):
        routes = routes_for(FILES)
        manifest = manifest_for(FILES)
        manifest["files"]["flip/create-model.gif"]["bytes"] += 1
        routes[f"{BASE}/{TAG}/manifest.json"] = [FakeResponse(body=json.dumps(manifest).encode())]
        with pytest.raises(fetcher.FetchError, match="create-model.gif.*bytes"):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert not (dest / "flip/create-model.gif").exists()

    def test_404_fails_without_retry(self, fetcher, dest):
        url = f"{BASE}/{TAG}/manifest.json"
        session = FakeSession({url: [FakeResponse(status_code=404)]})
        with pytest.raises(fetcher.FetchError, match="HTTP 404.*revision"):
            run_fetch(fetcher, dest, session)
        assert session.calls == [url]


class TestFastPath:
    def test_second_build_at_a_tag_makes_no_requests(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        report, _ = run_fetch(fetcher, dest, FakeSession())  # no routes: any GET raises
        assert report.fetched == []
        assert sorted(report.verified) == sorted(FILES)

    def test_a_file_whose_local_bytes_differ_is_refetched_alone(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        (dest / "admin/create-user.gif").write_bytes(b"locally re-recorded")
        url = f"{BASE}/{TAG}/admin/create-user.gif"
        session = FakeSession({url: [FakeResponse(body=FILES["admin/create-user.gif"])]})
        report, _ = run_fetch(fetcher, dest, session)
        assert session.calls == [url]
        assert report.fetched == ["admin/create-user.gif"]
        assert (dest / "admin/create-user.gif").read_bytes() == FILES["admin/create-user.gif"]

    def test_a_same_length_local_file_with_different_bytes_is_refetched(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        original = FILES["flip/create-model.gif"]
        (dest / "flip/create-model.gif").write_bytes(bytes(reversed(original)))
        url = f"{BASE}/{TAG}/flip/create-model.gif"
        report, _ = run_fetch(fetcher, dest, FakeSession({url: [FakeResponse(body=original)]}))
        assert report.fetched == ["flip/create-model.gif"]
        assert (dest / "flip/create-model.gif").read_bytes() == original

    def test_a_non_tag_revision_always_rereads_the_manifest(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        session = FakeSession(routes_for(FILES, revision="main"))
        report, _ = run_fetch(fetcher, dest, session, revision="main")
        assert session.calls == [f"{BASE}/main/manifest.json"]
        assert report.fetched == []

    def test_a_new_version_refetches_only_the_files_whose_hash_changed(self, fetcher, dest):
        run_fetch(fetcher, dest, FakeSession(routes_for(FILES)))
        new_tag = "20260918T101500Z-946490c"
        changed = {**FILES, "flip/create-model.gif": b"GIF89a-flip-v2" * 2000}
        routes = routes_for(changed, revision=new_tag, version=new_tag)
        del routes[f"{BASE}/{new_tag}/admin/create-user.gif"]  # must not be requested
        session = FakeSession(routes)
        report, _ = run_fetch(fetcher, dest, session, revision=new_tag)
        assert report.fetched == ["flip/create-model.gif"]
        assert report.verified == ["admin/create-user.gif"]
        assert json.loads((dest / "manifest.json").read_text())["version"] == new_tag

    def test_a_corrupt_local_manifest_is_ignored(self, fetcher, dest):
        dest.mkdir()
        (dest / "manifest.json").write_text("{not json")
        session = FakeSession(routes_for(FILES))
        report, _ = run_fetch(fetcher, dest, session)
        assert session.calls[0] == f"{BASE}/{TAG}/manifest.json"
        assert sorted(report.fetched) == sorted(FILES)


class TestRetries:
    def test_429_honours_retry_after_and_succeeds_on_the_third_attempt(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{BASE}/{TAG}/manifest.json"
        good = routes[url][0]
        routes[url] = [FakeResponse(status_code=429, headers={"Retry-After": "7"}), FakeResponse(status_code=503), good]
        session = FakeSession(routes)
        _, sleeps = run_fetch(fetcher, dest, session)
        assert session.calls[:3] == [url, url, url]
        assert sleeps == [7.0, 2.0]

    def test_retry_after_is_capped(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{BASE}/{TAG}/manifest.json"
        good = routes[url][0]
        routes[url] = [FakeResponse(status_code=429, headers={"Retry-After": "999"}), good]
        _, sleeps = run_fetch(fetcher, dest, FakeSession(routes))
        assert sleeps == [float(fetcher.MAX_RETRY_AFTER_SECONDS)]

    def test_connection_errors_are_retried_then_give_up(self, fetcher, dest):
        url = f"{BASE}/{TAG}/manifest.json"
        session = FakeSession({url: [requests.ConnectionError("refused")] * fetcher.ATTEMPTS})
        with pytest.raises(fetcher.FetchError, match=f"giving up after {fetcher.ATTEMPTS} attempts"):
            run_fetch(fetcher, dest, session)
        assert session.calls == [url] * fetcher.ATTEMPTS

    def test_a_mid_transfer_failure_is_retried(self, fetcher, dest):
        routes = routes_for(FILES)
        url = f"{BASE}/{TAG}/flip/create-model.gif"
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
            f"{BASE}/{TAG}/manifest.json": [FakeResponse(body=manifest_with(escaped, sha(data), len(data)))],
            f"{BASE}/{TAG}/{escaped}": [FakeResponse(body=data)],
        }
        with pytest.raises(fetcher.FetchError):
            run_fetch(fetcher, dest, FakeSession(routes))
        assert [p.name for p in tmp_path.iterdir() if p.name != dest.name] == []
        assert not dest.exists() or not list(dest.rglob("*"))

    def test_a_valid_manifest_round_trips(self, fetcher):
        manifest = manifest_for(FILES)
        assert fetcher.parse_manifest(json.dumps(manifest).encode(), "m") == manifest


class TestRevisionAndRepo:
    def test_the_pin_is_one_stripped_line(self, fetcher, tmp_path):
        pin = tmp_path / ".gifs_version"
        pin.write_text(f"  {TAG}\n\n")
        assert fetcher.pinned_revision(pin) == TAG

    @pytest.mark.parametrize("text", ["", "\n", f"{TAG}\nother\n"])
    def test_an_empty_or_multi_line_pin_is_refused(self, fetcher, tmp_path, text):
        pin = tmp_path / ".gifs_version"
        pin.write_text(text)
        with pytest.raises(fetcher.FetchError, match="exactly one line"):
            fetcher.pinned_revision(pin)

    def test_a_missing_pin_is_refused(self, fetcher, tmp_path):
        with pytest.raises(fetcher.FetchError, match="cannot read"):
            fetcher.pinned_revision(tmp_path / "absent")

    def test_revision_precedence_is_explicit_then_env_then_pin(self, fetcher, tmp_path, monkeypatch):
        pin = tmp_path / ".gifs_version"
        pin.write_text(f"{TAG}\n")
        monkeypatch.delenv(fetcher.REVISION_ENV, raising=False)
        assert fetcher.resolve_revision(None, pin) == TAG
        monkeypatch.setenv(fetcher.REVISION_ENV, "main")
        assert fetcher.resolve_revision(None, pin) == "main"
        assert fetcher.resolve_revision("abc1234", pin) == "abc1234"

    def test_repo_from_env(self, fetcher, monkeypatch):
        monkeypatch.delenv(fetcher.REPO_ENV, raising=False)
        assert fetcher.repo_from_env() == "aicentreflip/docs-gifs"
        monkeypatch.setenv(fetcher.REPO_ENV, "someone/fork")
        assert fetcher.repo_from_env() == "someone/fork"

    def test_the_default_destination_is_the_gitignored_generated_tree(self, fetcher):
        assert fetcher.DEFAULT_DEST == fetcher.DOCS_DIR / "source" / "assets" / "generated" / "gifs"


class TestCli:
    def test_success_exits_zero(self, fetcher, dest, tmp_path, monkeypatch, capsys):
        pin = tmp_path / ".gifs_version"
        pin.write_text(f"{TAG}\n")
        monkeypatch.delenv(fetcher.REVISION_ENV, raising=False)
        monkeypatch.delenv(fetcher.REPO_ENV, raising=False)
        monkeypatch.setattr(fetcher.requests, "Session", lambda: FakeSession(routes_for(FILES)))
        assert fetcher.main(["--dest", str(dest), "--pin-file", str(pin)]) == 0
        assert "2 fetched" in capsys.readouterr().out
        assert (dest / "manifest.json").exists()

    def test_failure_exits_one_naming_the_url(self, fetcher, dest, monkeypatch, capsys):
        url = f"{BASE}/{TAG}/manifest.json"
        monkeypatch.setattr(fetcher.requests, "Session", lambda: FakeSession({url: [FakeResponse(status_code=404)]}))
        assert fetcher.main(["--dest", str(dest), "--revision", TAG]) == 1
        assert url in capsys.readouterr().err
