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
#

"""Pin download_data.download's resume-and-verify contract.

A PI-CAI fold is ~5 GB. The fetch must resume a partial file with a Range request rather than
restart, abandon a socket that goes quiet (a read timeout is passed to requests), retry a
transfer that ended short of the announced size, and never leave a short file under the final
name. The server is faked: a stub session whose ``get`` returns canned responses in order.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import requests

PROSTATE_DIR = Path(__file__).resolve().parents[3] / "datasets" / "prostate"


def load_script(name: str) -> ModuleType:
    if str(PROSTATE_DIR) not in sys.path:
        sys.path.insert(0, str(PROSTATE_DIR))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", PROSTATE_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def download_data() -> ModuleType:
    return load_script("download_data")


class FakeResponse:
    """The parts of requests.Response the downloader touches: status, headers, streaming body."""

    def __init__(
        self, status: int, body: bytes, headers: dict[str, str] | None = None, *, truncate_after: int | None = None
    ):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._truncate_after = truncate_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def iter_content(self, chunk_size: int):
        sent = 0
        for start in range(0, len(self._body), chunk_size):
            chunk = self._body[start : start + chunk_size]
            if self._truncate_after is not None and sent + len(chunk) > self._truncate_after:
                chunk = chunk[: self._truncate_after - sent]
                if chunk:
                    yield chunk
                # The connection goes quiet: requests raises a read timeout.
                raise requests.ConnectionError("read timed out")
            sent += len(chunk)
            yield chunk


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, *, stream, headers, timeout):
        self.calls.append({"url": url, "stream": stream, "headers": dict(headers), "timeout": timeout})
        return self._responses.pop(0)


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch, download_data: ModuleType) -> None:
    monkeypatch.setattr(download_data.time, "sleep", lambda _s: None)
    monkeypatch.setattr(download_data, "CHUNK", 4)


def _install(monkeypatch: pytest.MonkeyPatch, download_data: ModuleType, responses: list[FakeResponse]) -> FakeSession:
    session = FakeSession(responses)
    monkeypatch.setattr(download_data.requests, "Session", lambda: session)
    return session


BODY = b"0123456789abcdef"


def test_fresh_download_streams_with_timeouts_and_renames_when_complete(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    session = _install(monkeypatch, download_data, [FakeResponse(200, BODY, {"Content-Length": str(len(BODY))})])
    dest = tmp_path / "fold.zip"

    download_data.download("https://example/fold.zip", dest)

    assert dest.read_bytes() == BODY
    assert not dest.with_name("fold.zip.part").exists()
    call = session.calls[0]
    assert call["stream"] is True
    assert call["headers"] == {}, "no Range on a fresh download"
    assert call["timeout"] == (download_data.CONNECT_TIMEOUT, download_data.READ_TIMEOUT)


def test_partial_file_is_resumed_with_a_range_request(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    dest = tmp_path / "fold.zip"
    dest.with_name("fold.zip.part").write_bytes(BODY[:6])
    session = _install(
        monkeypatch,
        download_data,
        [FakeResponse(206, BODY[6:], {"Content-Range": f"bytes 6-{len(BODY) - 1}/{len(BODY)}"})],
    )

    download_data.download("https://example/fold.zip", dest)

    assert session.calls[0]["headers"] == {"Range": "bytes=6-"}
    assert dest.read_bytes() == BODY


def test_server_ignoring_range_restarts_from_zero(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    dest = tmp_path / "fold.zip"
    dest.with_name("fold.zip.part").write_bytes(b"stale")
    _install(monkeypatch, download_data, [FakeResponse(200, BODY, {"Content-Length": str(len(BODY))})])

    download_data.download("https://example/fold.zip", dest)

    assert dest.read_bytes() == BODY, "the stale partial content was discarded, not prepended"


def test_stalled_transfer_is_retried_from_where_it_stopped(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    """The fold-1 hang: bytes stop arriving short of the end. The next attempt resumes, not restarts."""
    dest = tmp_path / "fold.zip"
    session = _install(
        monkeypatch,
        download_data,
        [
            FakeResponse(200, BODY, {"Content-Length": str(len(BODY))}, truncate_after=10),
            FakeResponse(206, BODY[10:], {"Content-Range": f"bytes 10-{len(BODY) - 1}/{len(BODY)}"}),
        ],
    )

    download_data.download("https://example/fold.zip", dest)

    assert dest.read_bytes() == BODY
    assert session.calls[1]["headers"] == {"Range": "bytes=10-"}


def test_short_body_with_a_clean_close_is_not_accepted(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    """A server that closes early without error still announced the size; the file must not be called done."""
    dest = tmp_path / "fold.zip"
    monkeypatch.setattr(download_data, "ATTEMPTS", 2)
    _install(
        monkeypatch,
        download_data,
        [
            FakeResponse(200, BODY[:12], {"Content-Length": str(len(BODY))}),
            FakeResponse(206, BODY[12:14], {"Content-Range": f"bytes 12-13/{len(BODY)}"}),
        ],
    )

    with pytest.raises(download_data.IncompleteDownload, match="14 of 16 bytes"):
        download_data.download("https://example/fold.zip", dest)
    assert not dest.exists()
    assert dest.with_name("fold.zip.part").stat().st_size == 14, "the partial bytes are kept for the next run"


def test_range_not_satisfiable_means_already_complete(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    dest = tmp_path / "fold.zip"
    dest.with_name("fold.zip.part").write_bytes(BODY)
    _install(monkeypatch, download_data, [FakeResponse(416, b"", {"Content-Range": f"bytes */{len(BODY)}"})])

    download_data.download("https://example/fold.zip", dest)

    assert dest.read_bytes() == BODY


def test_unknown_total_is_accepted_as_sent(
    download_data: ModuleType, monkeypatch: pytest.MonkeyPatch, fast_retries: None, tmp_path: Path
) -> None:
    """GitHub's generated archive zip carries no Content-Length: nothing to verify against, take the body."""
    dest = tmp_path / "labels.zip"
    _install(monkeypatch, download_data, [FakeResponse(200, BODY, {})])

    download_data.download("https://example/main.zip", dest)

    assert dest.read_bytes() == BODY
