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
staging contract with a stand-in file — no network, no real checkpoint.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import torch

FL_TUTORIALS = Path(__file__).resolve().parents[1]
APP_DIR = FL_TUTORIALS / "nvflare/image_synthesis/latent_diffusion_model/app_files"


@pytest.fixture(scope="module")
def trainer():
    # trainer.py imports its flat siblings (models, transforms, validator) by bare name, as the
    # deployed job does; put the app dir first on the path for the duration of the import.
    sys.path.insert(0, str(APP_DIR))
    try:
        spec = importlib.util.spec_from_file_location("ldm_trainer_under_test", APP_DIR / "trainer.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(APP_DIR))
    return module


def test_backbone_is_the_shippable_one(trainer):
    assert trainer.PERCEPTUAL_BACKBONE == "squeeze"
    assert trainer.HUB_CHECKPOINT == "squeezenet1_1-b8a52dc0.pth"


def test_staging_copies_the_shipped_file_into_the_hub_layout(trainer, tmp_path, monkeypatch):
    seen: dict[str, str] = {}
    monkeypatch.setattr(torch.hub, "set_dir", lambda d: seen.setdefault("hub_dir", d))
    shipped = tmp_path / trainer.HUB_CHECKPOINT
    shipped.write_bytes(b"not really a checkpoint")

    target = trainer.stage_perceptual_backbone(tmp_path)

    assert target == tmp_path / "torch_hub" / "checkpoints" / trainer.HUB_CHECKPOINT
    assert target.read_bytes() == b"not really a checkpoint"
    assert seen["hub_dir"] == str(tmp_path / "torch_hub"), "torch.hub must be pointed at the app's hub dir"
    # Idempotent: a second call neither fails nor re-copies over a file torchvision may have verified.
    target.write_bytes(b"already staged")
    assert trainer.stage_perceptual_backbone(tmp_path) == target
    assert target.read_bytes() == b"already staged"


def test_missing_checkpoint_fails_loudly_and_names_the_remedy(trainer, tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        trainer.stage_perceptual_backbone(tmp_path)
    message = str(excinfo.value)
    assert trainer.HUB_CHECKPOINT in message
    assert "make weights" in message
    assert "FLIP#1206" in message
    assert not (tmp_path / "torch_hub").exists(), "nothing should be created when the shipped file is absent"
