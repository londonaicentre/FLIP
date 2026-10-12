# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""``datasets/weights/fetch_weights.py`` — the host-side half of the offline-apps rule (FLIP#1206).

The script stages the torchvision checkpoint a tutorial ships beside its code. These tests pin
the contract without touching the network: the hash prefix comes from the filename and is what
the download is checked against, the file lands under the requested directory with the name
torch.hub expects, an existing file is re-verified rather than trusted, and a failed download
stages nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from tutorial_apps import TUTORIALS_ROOT, load_module

fetch_weights = load_module("fetch_weights_under_test", TUTORIALS_ROOT / "datasets" / "weights" / "fetch_weights.py")


def _bytes_with_prefix(prefix: str) -> bytes:
    """Some bytes whose sha256 starts with ``prefix`` — brute-forced, so keep the prefix short."""
    for n in range(10_000_000):
        candidate = f"checkpoint {n}".encode()
        if hashlib.sha256(candidate).hexdigest().startswith(prefix):
            return candidate
    raise AssertionError("no preimage found")


@pytest.fixture(scope="module")
def good_bytes() -> bytes:
    # Two hex chars are enough to exercise the check without a long search.
    return _bytes_with_prefix("ab")


@pytest.fixture
def spec(monkeypatch, good_bytes):
    """A checkpoint whose URL name carries the (short) hash prefix of ``good_bytes``."""
    spec = fetch_weights.Checkpoint("https://download.pytorch.org/models/tiny-ab.pth")
    monkeypatch.setitem(fetch_weights.CHECKPOINTS, "tiny", spec)
    return spec


def test_known_checkpoints_carry_torch_hubs_hash_prefix():
    """The prefix torch.hub embeds in the filename is the whole integrity story; every entry needs one."""
    for arch, checkpoint in fetch_weights.CHECKPOINTS.items():
        assert checkpoint.url.startswith("https://download.pytorch.org/models/"), arch
        assert len(checkpoint.hash_prefix) == 8, arch
        assert checkpoint.filename.endswith(f"-{checkpoint.hash_prefix}.pth"), arch
    assert "squeezenet1_1" in fetch_weights.CHECKPOINTS


def test_a_checkpoint_without_a_hash_prefix_is_refused():
    with pytest.raises(ValueError, match="no hash prefix"):
        _ = fetch_weights.Checkpoint("https://example.org/plain.pth").hash_prefix


def test_stage_downloads_with_the_hash_prefix_and_renames_into_place(spec, tmp_path, monkeypatch, good_bytes):
    calls: list[dict] = []

    def fake_download(url, dst, **kwargs):
        calls.append({"url": url, "hash_prefix": kwargs["hash_prefix"]})
        Path(dst).write_bytes(good_bytes)

    monkeypatch.setattr(fetch_weights, "download_url_to_file", fake_download)
    target = fetch_weights.stage("tiny", tmp_path / "weights")
    assert target == tmp_path / "weights" / "tiny-ab.pth"
    assert target.read_bytes() == good_bytes
    assert calls == [{"url": spec.url, "hash_prefix": "ab"}], (
        "the hash in the filename must be what the download checks"
    )
    assert sorted(p.name for p in target.parent.iterdir()) == ["tiny-ab.pth"], "no .part file left behind"


def test_stage_reuses_an_existing_file_only_after_rehashing_it(spec, tmp_path, monkeypatch, good_bytes):
    monkeypatch.setattr(fetch_weights, "download_url_to_file", lambda *a, **k: pytest.fail("must not download"))
    target = tmp_path / spec.filename
    target.write_bytes(good_bytes)
    assert fetch_weights.stage("tiny", tmp_path) == target

    target.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="expected ab"):
        fetch_weights.stage("tiny", tmp_path)


def test_a_failed_download_stages_nothing(spec, tmp_path, monkeypatch):
    def refuse(url, dst, **kwargs):
        Path(dst).write_bytes(b"partial")
        raise RuntimeError("invalid hash value")

    monkeypatch.setattr(fetch_weights, "download_url_to_file", refuse)
    with pytest.raises(RuntimeError, match="invalid hash value"):
        fetch_weights.stage("tiny", tmp_path / "weights")
    assert not (tmp_path / "weights" / spec.filename).exists(), "the final name must never hold a partial file"


def test_cli_stages_the_named_checkpoint(spec, tmp_path, monkeypatch, good_bytes, capsys):
    monkeypatch.setattr(fetch_weights, "download_url_to_file", lambda url, dst, **k: Path(dst).write_bytes(good_bytes))
    fetch_weights.main(["tiny", "--out", str(tmp_path / "out")])
    assert (tmp_path / "out" / spec.filename).read_bytes() == good_bytes
    assert "tiny-ab.pth" in capsys.readouterr().out


def test_cli_refuses_an_unknown_architecture(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        fetch_weights.main(["not-a-network", "--out", str(tmp_path)])
    assert excinfo.value.code == 2
