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

"""A 3-D MedicalNet perceptual loss built from weights that ship with the app.

MedicalNet: Chen, Ma and Zheng, "Med3D: Transfer Learning for 3D Medical Image Analysis",
arXiv:1904.00625 (2019); weights released by Tencent under the MIT licence.

MONAI's ``PerceptualLoss(spatial_dims=3, network_type="medicalnet_...")`` builds its backbone with
``torch.hub.load``, which downloads at run time, and FL apps never download at run time (FLIP#1206).
This module builds the same network from MONAI's ``ResNetFeatures`` instead and loads the uploaded
checkpoint, refusing a partial load. The scoring matches MONAI's ``MedicalNetPerceptualSimilarity``:
per-volume standardisation, the final feature map only, L2-normalised across channels, squared
difference summed over channels and averaged over space. On the 96x112x80 grid that final map is
2048 x 3 x 4 x 3, so the loss scores global structure rather than texture.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import torch
from monai.networks.nets import ResNetFeatures
from torch import nn

logger = logging.getLogger(__name__)

# The ResNet-50 the reference MSD brain recipe uses, staged by `make weights` (fl-tutorials/datasets/weights)
# under torch.hub's naming, `<arch>-<first 8 sha256 hex>.pth`.
MEDICALNET_ARCH = "resnet50"
MEDICALNET_CHECKPOINT = "medicalnet_resnet50_23datasets-ff48a622.pth"
MEDICALNET_SHA256_PREFIX = "ff48a622"

# Zero in float16, so every expression using it casts to float32 first.
_EPS = 1e-10


def _intensity_normalisation(volume: torch.Tensor) -> torch.Tensor:
    """Standardise a whole volume to zero mean and unit variance, as MedicalNet's inputs were.

    As MONAI's, plus an epsilon so a constant volume (an undertrained decoder's output) gives zeros
    rather than ``0/0 = NaN``.
    """
    volume = volume.float()
    return (volume - volume.mean()) / (volume.std() + _EPS)


def _normalize_tensor(x: torch.Tensor) -> torch.Tensor:
    """L2-normalise across the channel axis, in float32: up to 2048 squared channels overflow float16."""
    x = x.float()
    norm_factor = torch.sqrt(torch.sum(x**2, dim=1, keepdim=True))
    return x / (norm_factor + _EPS)


def _verify_hash_prefix(path: Path) -> None:
    """Raise unless ``path``'s sha256 starts with the prefix in its name: catches a swapped or partial file."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith(MEDICALNET_SHA256_PREFIX):
        raise RuntimeError(
            f"{path} has sha256 {digest[: len(MEDICALNET_SHA256_PREFIX)]}…, expected "
            f"{MEDICALNET_SHA256_PREFIX}…; delete it and re-run `make weights` (FLIP#1206)."
        )


class MedicalNetPerceptualLoss(nn.Module):
    """Perceptual distance between two 3-D volumes, measured by a frozen MedicalNet ResNet-50."""

    def __init__(self, checkpoint: Path) -> None:
        super().__init__()
        model = ResNetFeatures(MEDICALNET_ARCH, pretrained=False, spatial_dims=3, in_channels=1)
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        # The published file wraps the tensors in a "state_dict" key and carries the `module.`
        # prefix left by the DataParallel wrapper it was trained under.
        state = state.get("state_dict", state)
        state = {key.removeprefix("module."): value for key, value in state.items()}
        missing, unexpected = model.load_state_dict(state, strict=False)
        if missing or unexpected:
            raise RuntimeError(
                f"{checkpoint.name} does not match MedicalNet {MEDICALNET_ARCH}: "
                f"{len(missing)} missing and {len(unexpected)} unexpected keys. A partial load would "
                "leave a randomly-initialised critic reporting plausible losses, so this refuses instead."
            )
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        self.model = model

    def forward(self, input: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """Return the mean perceptual distance for a batch of ``(B, C, D, H, W)`` volumes."""
        input = _intensity_normalisation(input)
        target = _intensity_normalisation(target)

        # One channel at a time, features concatenated, as the reference does.
        feats_input = torch.cat(
            [self.model(input[:, idx, ...].unsqueeze(1))[-1] for idx in range(input.shape[1])], dim=1
        )
        feats_target = torch.cat(
            [self.model(target[:, idx, ...].unsqueeze(1))[-1] for idx in range(target.shape[1])], dim=1
        )

        difference = (_normalize_tensor(feats_input) - _normalize_tensor(feats_target)) ** 2
        return difference.sum(dim=1, keepdim=True).mean([2, 3, 4], keepdim=True).mean()


def load_medicalnet_perceptual(working_dir: Path) -> MedicalNetPerceptualLoss:
    """Build the loss from the checkpoint uploaded beside the training script (flat, like every app file)."""
    shipped = working_dir / MEDICALNET_CHECKPOINT
    if not shipped.is_file():
        raise FileNotFoundError(
            f"{MEDICALNET_CHECKPOINT} is missing from {working_dir}. FL apps do not download weights at "
            "run time (FLIP#1206): run `make weights` in the tutorial directory to stage it, and upload "
            "it together with the other app files."
        )
    _verify_hash_prefix(shipped)
    logger.info("MedicalNet perceptual backbone loaded (frozen, 3-D, final feature map).")
    return MedicalNetPerceptualLoss(shipped)
