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
the contract without touching the network: the hash check is anchored in the filename, the
file lands under the requested directory with the name torch.hub expects, and the cache is reused.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

FL_TUTORIALS = Path(__file__).resolve().parents[1]
SCRIPT = FL_TUTORIALS / "datasets" / "weights" / "fetch_weights.py"


@pytest.fixture(scope="module")
def fetch_weights():
    spec = importlib.util.spec_from_file_location("fetch_weights_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_known_checkpoints_carry_their_hash_prefix(fetch_weights):
    """torchvision embeds the first 8 sha256 hex chars in the filename; check_hash relies on it."""
    for arch, spec in fetch_weights.CHECKPOINTS.items():
        assert spec.url.startswith("https://download.pytorch.org/models/"), arch
        assert spec.filename.split("-")[-1].split(".")[0] == spec.sha256_prefix, arch
        assert len(spec.sha256_prefix) == 8, arch
    assert "squeezenet1_1" in fetch_weights.CHECKPOINTS


def test_stage_copies_the_cached_file_under_its_torchvision_name(fetch_weights, tmp_path, monkeypatch):
    spec = fetch_weights.CHECKPOINTS["squeezenet1_1"]
    calls: list[dict] = []

    def fake_download(url, model_dir, check_hash, progress, map_location):
        calls.append({"url": url, "check_hash": check_hash})
        Path(model_dir).mkdir(parents=True, exist_ok=True)
        (Path(model_dir) / spec.filename).write_bytes(b"checkpoint bytes")
        return {}

    monkeypatch.setattr(fetch_weights, "load_state_dict_from_url", fake_download)
    target = fetch_weights.stage("squeezenet1_1", tmp_path / "cache", tmp_path / "out" / "checkpoints")
    assert target == tmp_path / "out" / "checkpoints" / spec.filename
    assert target.read_bytes() == b"checkpoint bytes"
    assert calls == [{"url": spec.url, "check_hash": True}], "the hash in the filename must be enforced"


def test_download_fails_loudly_if_torch_hub_left_nothing_behind(fetch_weights, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_weights, "load_state_dict_from_url", lambda *a, **k: {})
    with pytest.raises(FileNotFoundError):
        fetch_weights.download(fetch_weights.CHECKPOINTS["squeezenet1_1"], tmp_path)
