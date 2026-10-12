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
"""The latent-diffusion tutorial's perceptual-loss backbone is shipped, not fetched (FLIP#1206).

lpips resolves its torchvision backbone through ``torch.hub``; the app pre-stages the shipped
checkpoint into a hub ``checkpoints/`` dir so that lookup never goes online. These tests pin the
staging contract — and, once, construct the real ``PerceptualLoss`` from a staged random-init
SqueezeNet with every download refused, so the offline claim is exercised rather than asserted.
No network, no real checkpoint (a random state dict stands in; the hash check has its own test).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest
import torch
import torchvision
from monai.losses import PerceptualLoss
from torchvision.models import SqueezeNet1_1_Weights
from tutorial_apps import TUTORIALS_ROOT, load_module

TUTORIAL_DIR = TUTORIALS_ROOT / "nvflare/image_synthesis/latent_diffusion_model"
APP_DIR = TUTORIAL_DIR / "app_files"
fetch_weights = load_module("fetch_weights_under_test", TUTORIALS_ROOT / "datasets" / "weights" / "fetch_weights.py")


@pytest.fixture(scope="module")
def trainer():
    # trainer.py imports its flat siblings (models, transforms, validator) by bare name, as the
    # deployed job does; put the app dir first on the path for the import, then drop the bare
    # names it registered so no other app's same-named module resolves to the LDM copy.
    before = set(sys.modules)
    sys.path.insert(0, str(APP_DIR))
    try:
        module = load_module("ldm_trainer_under_test", APP_DIR / "trainer.py")
    finally:
        sys.path.remove(str(APP_DIR))
        for name in set(sys.modules) - before - {"ldm_trainer_under_test"}:
            if "." not in name:
                sys.modules.pop(name, None)
    return module


@pytest.fixture
def hub_dir_restored(monkeypatch):
    """`torch.hub.set_dir` is process-global; put it back after the test."""
    monkeypatch.setattr(torch.hub, "_hub_dir", None)


@pytest.fixture
def hash_check_skipped(trainer, monkeypatch):
    """A random state dict cannot carry torchvision's sha256 prefix; the verifier has its own test."""
    monkeypatch.setattr(trainer, "_verify_hash_prefix", lambda path: None)


def test_shipped_filename_is_the_one_torchvision_asks_for(trainer):
    """Three spellings, one truth: a torchvision URL bump must fail here, not download on a dev box."""
    expected = Path(SqueezeNet1_1_Weights.IMAGENET1K_V1.url).name
    assert trainer.HUB_CHECKPOINT == expected
    assert fetch_weights.CHECKPOINTS["squeezenet1_1"].filename == expected
    assert trainer.PERCEPTUAL_BACKBONE == "squeeze"  # the lpips net that loads SqueezeNet1_1


def test_make_weights_produces_the_file_the_trainer_loads(trainer):
    makefile = (TUTORIAL_DIR / "Makefile").read_text(encoding="utf-8")
    assert trainer.HUB_CHECKPOINT in makefile, "the Makefile must name the exact file trainer.py loads"
    assert re.search(r"^weights:", makefile, re.M), "no `weights` target"
    # Both bundling paths need the file in app_files/ first.
    assert re.search(r"^sim: weights", makefile, re.M)
    assert re.search(r"^export: weights", makefile, re.M)


def test_staging_copies_the_shipped_file_into_the_hub_layout(trainer, tmp_path, hub_dir_restored, hash_check_skipped):
    shipped = tmp_path / trainer.HUB_CHECKPOINT
    shipped.write_bytes(b"not really a checkpoint")

    target = trainer.stage_perceptual_backbone(tmp_path)

    assert target == tmp_path / "torch_hub" / "checkpoints" / trainer.HUB_CHECKPOINT
    assert target.read_bytes() == b"not really a checkpoint"
    assert Path(torch.hub.get_dir()) == tmp_path / "torch_hub", "torch.hub must resolve to the app's hub dir"
    assert sorted(p.name for p in target.parent.iterdir()) == [trainer.HUB_CHECKPOINT], "no .part left behind"
    # Idempotent: a second call neither fails nor re-copies over a file already in place.
    target.write_bytes(b"already staged")
    assert trainer.stage_perceptual_backbone(tmp_path) == target
    assert target.read_bytes() == b"already staged"


def test_perceptual_loss_builds_offline_from_the_staged_file(
    trainer, tmp_path, hub_dir_restored, hash_check_skipped, monkeypatch
):
    """The whole point: lpips finds the staged file and never reaches for the network."""
    torch.save(torchvision.models.squeezenet1_1().state_dict(), tmp_path / trainer.HUB_CHECKPOINT)
    monkeypatch.setattr(torch.hub, "download_url_to_file", lambda *a, **k: pytest.fail("attempted a download"))

    trainer.stage_perceptual_backbone(tmp_path)
    loss = PerceptualLoss(2, network_type=trainer.PERCEPTUAL_BACKBONE)

    value = loss(torch.rand(1, 1, 32, 32), torch.rand(1, 1, 32, 32))
    assert torch.isfinite(value)


def test_missing_checkpoint_fails_loudly_and_names_the_remedy(trainer, tmp_path, hub_dir_restored):
    with pytest.raises(FileNotFoundError) as excinfo:
        trainer.stage_perceptual_backbone(tmp_path)
    message = str(excinfo.value)
    assert trainer.HUB_CHECKPOINT in message
    assert "make weights" in message
    assert "FLIP#1206" in message
    assert not (tmp_path / "torch_hub").exists(), "nothing should be created when the shipped file is absent"


def test_a_torchvision_url_bump_is_refused_not_downloaded(trainer, tmp_path, hub_dir_restored, monkeypatch):
    monkeypatch.setattr(trainer, "HUB_CHECKPOINT", "squeezenet1_1-deadbeef.pth")
    (tmp_path / "squeezenet1_1-deadbeef.pth").write_bytes(b"whatever")
    with pytest.raises(RuntimeError, match="torchvision now loads"):
        trainer.stage_perceptual_backbone(tmp_path)


def test_a_corrupt_shipped_file_is_refused_with_the_remedy(trainer, tmp_path, hub_dir_restored):
    shipped = tmp_path / trainer.HUB_CHECKPOINT
    shipped.write_bytes(b"not torchvision's bytes")
    with pytest.raises(RuntimeError, match="make weights"):
        trainer.stage_perceptual_backbone(tmp_path)
    assert not (tmp_path / "torch_hub").exists()


def test_hash_prefix_verifier_accepts_a_matching_file(trainer, tmp_path):
    payload = next(
        f"x {n}".encode() for n in range(1_000_000) if hashlib.sha256(f"x {n}".encode()).hexdigest()[:2] == "ab"
    )
    good = tmp_path / "tiny-ab.pth"
    good.write_bytes(payload)
    trainer._verify_hash_prefix(good)  # no raise
    with pytest.raises(RuntimeError, match="no sha256 prefix"):
        trainer._verify_hash_prefix(tmp_path / "unnamed.pth")
