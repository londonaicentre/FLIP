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

"""The autoencoder tutorial's perceptual-loss backbone is shipped, not fetched (FLIP#1206).

The loss is MedicalNet ResNet-10, a genuinely 3-D network. MONAI exposes it as
``PerceptualLoss(spatial_dims=3, network_type="medicalnet_resnet10_23datasets")``, but that
constructor reaches the network twice over — ``torch.hub.load`` fetches the
``Project-MONAI/perceptual-models`` *repository*, whose ``download_model`` then pulls the weights
from Hugging Face. Neither can happen inside a job: a platform FL server has no internet route, a
trust host sits behind a hospital firewall, and a run-time fetch bypasses the scanned upload path
entirely, so the file a Trust inspected is no longer the file that runs.

``app_files/medicalnet_perceptual.py`` therefore builds the same architecture from MONAI's own
``ResNetFeatures`` — which takes an architecture *name*, never a URL — and ``make weights`` converts
the published checkpoint into the job's ``SERVER_CHECKPOINT``, which reaches the trusts in the
round-0 global model. These tests pin the ways that arrangement can rot silently: the file names
drifting between the fetcher, the Makefile and the module, a partial state-dict load or a missing
checkpoint leaving a randomly-initialised critic, and the constant-volume NaN that would take the
whole run down through the trainer's fatal-NaN guard.
"""

from __future__ import annotations

import json
import sys

import pytest
import torch
from tutorial_apps import TUTORIALS_ROOT, load_module

# The perceptual loss lives here, and only here: the two diffusion tutorials score in latent/noise
# space and construct no perceptual loss at all.
TUTORIAL_DIR = TUTORIALS_ROOT / "nvflare/image_generation/brain_mri_ldm_stage1_autoencoder"
APP_DIR = TUTORIAL_DIR / "app_files"
SHARED_WEIGHTS = TUTORIALS_ROOT / "data" / "weights"
fetch_weights = load_module("fetch_weights_under_test", TUTORIALS_ROOT / "datasets" / "weights" / "fetch_weights.py")


# Derived from the app module rather than hardcoded, so switching MedicalNet depth is a one-line
# change there and these tests follow it instead of pinning the old choice.
def _arch(perceptual) -> str:
    return f"medicalnet_{perceptual.MEDICALNET_ARCH}"


@pytest.fixture
def perceptual():
    """Import the app module by its bare name, the way the trainer does at run time."""
    sys.path.insert(0, str(APP_DIR))
    try:
        yield load_module("medicalnet_perceptual_under_test", APP_DIR / "medicalnet_perceptual.py")
    finally:
        sys.path.remove(str(APP_DIR))


def test_make_weights_writes_the_file_the_server_loads(perceptual):
    """The fetcher's file is the one the converter reads, and the converter writes ``SERVER_CHECKPOINT``.

    A drift here is invisible until a job runs: the server loads nothing into the backbone, and every
    trust then refuses the random weights it receives, after upload, rather than in the tutorial directory.
    """
    assert fetch_weights.CHECKPOINTS[_arch(perceptual)].filename == perceptual.MEDICALNET_CHECKPOINT
    makefile = (TUTORIAL_DIR / "Makefile").read_text()
    assert f"HUB_CHECKPOINT := {perceptual.MEDICALNET_CHECKPOINT}" in makefile
    assert f"PERCEPTUAL_CHECKPOINT := {perceptual.PERCEPTUAL_CHECKPOINT}" in makefile
    assert f"download-weights ARCH={_arch(perceptual)}" in makefile
    config = json.loads((APP_DIR / "config.json").read_text())
    assert config["SERVER_CHECKPOINT"] == perceptual.PERCEPTUAL_CHECKPOINT


def test_staged_name_carries_the_digest_the_module_verifies(perceptual):
    """The filename's hash prefix is the one checked at load, so a swapped file cannot pass."""
    assert fetch_weights.CHECKPOINTS[_arch(perceptual)].hash_prefix == perceptual.MEDICALNET_SHA256_PREFIX
    assert perceptual.MEDICALNET_SHA256_PREFIX in perceptual.MEDICALNET_CHECKPOINT


def test_the_backbone_builds_without_touching_the_network(perceptual):
    """``ResNetFeatures`` takes an architecture name, so construction cannot download.

    This is the property that makes the whole approach viable where the MONAI constructor is not.
    """
    from monai.networks.nets import ResNetFeatures

    # `.eval()` matters: BatchNorm refuses a 1-element spatial map while training, and the deepest
    # feature map of a small volume is exactly that. The loss always runs the backbone frozen.
    model = ResNetFeatures(perceptual.MEDICALNET_ARCH, pretrained=False, spatial_dims=3, in_channels=1).eval()
    outs = model(torch.zeros(1, 1, 64, 64, 64))
    # Five scales are exposed; the loss deliberately scores only the last, as MONAI's does.
    assert len(outs) == 5


def test_a_backbone_the_server_did_not_fill_is_refused_with_the_remedy(perceptual):
    """No silent training against random weights, and the message says how to fix it.

    The server loads ``SERVER_CHECKPOINT`` with ``strict=False``: a checkpoint it never found leaves
    the backbone as built, and that is what a trust would receive.
    """
    with pytest.raises(RuntimeError) as excinfo:
        perceptual.verify_backbone(perceptual.build_backbone())
    message = str(excinfo.value)
    assert "make weights" in message
    assert perceptual.PERCEPTUAL_CHECKPOINT in message
    assert "FLIP#1206" in message


def test_the_backbone_is_built_frozen(perceptual):
    """The trainer never switches it to training mode, so it has to start in eval with no gradients."""
    backbone = perceptual.build_backbone()
    assert not backbone.training
    assert not any(parameter.requires_grad for parameter in backbone.parameters())


def test_a_corrupt_published_file_is_refused_with_the_remedy(perceptual, tmp_path):
    """A file under the right name but the wrong content raises instead of converting."""
    (tmp_path / perceptual.MEDICALNET_CHECKPOINT).write_bytes(b"not a checkpoint")
    with pytest.raises(RuntimeError, match="expected"):
        perceptual.read_published_checkpoint(tmp_path / perceptual.MEDICALNET_CHECKPOINT)


def test_a_partial_state_dict_is_refused_rather_than_half_loaded(perceptual, tmp_path, monkeypatch):
    """A mismatched checkpoint must raise, not load ``strict=False`` into a random critic.

    This is the failure mode worth a test of its own: a perceptual loss backed by random weights
    still returns small, smoothly-decreasing numbers, so nothing downstream looks wrong.
    """
    from monai.networks.nets import ResNetFeatures

    monkeypatch.setattr(perceptual, "_verify_hash_prefix", lambda path: None)
    reference = ResNetFeatures(perceptual.MEDICALNET_ARCH, pretrained=False, spatial_dims=3, in_channels=1)
    # Correctly *shaped* tensors, but only some of them: a shape clash would raise on its own, and
    # what has to be caught here is the subtler case that `strict=False` would happily swallow.
    partial = {f"module.{k}": v for k, v in list(reference.state_dict().items())[:5]}
    torch.save({"state_dict": partial}, tmp_path / perceptual.MEDICALNET_CHECKPOINT)
    with pytest.raises(RuntimeError, match="missing"):
        perceptual.read_published_checkpoint(tmp_path / perceptual.MEDICALNET_CHECKPOINT)


def test_a_constant_volume_does_not_produce_nan(perceptual):
    """Standardisation must not divide by a zero standard deviation.

    MONAI's reference divides by a bare ``std()``, so a constant volume yields ``0/0``. An
    undertrained decoder does emit near-constant output, and the trainer treats any NaN as fatal —
    so the reference's own formula would be a liveness bug here.
    """
    flat = torch.full((1, 1, 8, 8, 8), 0.3)
    assert torch.isfinite(perceptual._intensity_normalisation(flat)).all()


def test_scoring_ranks_a_flat_fill_worse_than_a_blur(perceptual):
    """The metric must order the degradations this tutorial actually hits.

    The autoencoder's characteristic failure is a correct silhouette filled with a constant, and the
    loss can only push the model off it if that scores *worse* than a blur of the real thing. The
    phantom is deliberately structured rather than random: blurring noise changes it more than
    flattening does, so noise would assert the opposite of what real anatomy gives.
    """
    checkpoint = SHARED_WEIGHTS / perceptual.MEDICALNET_CHECKPOINT
    if not checkpoint.is_file():
        pytest.skip("MedicalNet checkpoint not downloaded; run `make weights` in the tutorial directory")
    backbone = perceptual.build_backbone()
    backbone.load_state_dict(perceptual.read_published_checkpoint(checkpoint))
    # The fingerprint the trusts check is the one these published weights carry.
    perceptual.verify_backbone(backbone)
    loss = perceptual.MedicalNetPerceptualLoss(backbone)

    volume = torch.zeros(1, 1, 64, 64, 64)
    volume[:, :, 10:54, 10:54, 10:54] = 0.4  # tissue
    volume[:, :, 22:42, 22:42, 22:42] = 0.9  # bright structure
    volume[:, :, 28:36, 28:36, 28:36] = 0.1  # ventricle
    flat_fill = (volume > 0).float() * volume[volume > 0].mean()

    identical = float(loss(volume, volume))
    mild_blur = float(loss(volume, torch.nn.functional.avg_pool3d(volume, 3, 1, 1)))
    heavy_blur = float(loss(volume, torch.nn.functional.avg_pool3d(volume, 7, 1, 3)))
    flat = float(loss(volume, flat_fill))

    assert identical == pytest.approx(0.0, abs=1e-6)
    assert identical < mild_blur < heavy_blur < flat
