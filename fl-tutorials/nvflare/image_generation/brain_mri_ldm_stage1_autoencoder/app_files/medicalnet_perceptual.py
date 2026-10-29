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

"""A 3-D MedicalNet perceptual loss whose weights reach the clients in the global model.

MedicalNet: Chen, Ma and Zheng, "Med3D: Transfer Learning for 3D Medical Image Analysis",
arXiv:1904.00625 (2019); weights released by Tencent under the MIT licence.

MONAI's ``PerceptualLoss(spatial_dims=3, network_type="medicalnet_...")`` builds its backbone with
``torch.hub.load``, which downloads at run time, and FL apps never download at run time (FLIP#1206).
This module builds the same network from MONAI's ``ResNetFeatures`` instead. Its 185 MB of weights
are too big for the job bundle (FLARE gives each client 10 s to acknowledge a deploy), so they travel
like the latent diffusion tutorial's frozen autoencoder: ``make weights`` converts the published file
into ``PERCEPTUAL_CHECKPOINT``, keyed for the model's ``perceptual`` submodule; ``config.json`` names
it ``SERVER_CHECKPOINT``, so the FL server loads it into the round-0 global model; and each client
checks what it received against ``MEDICALNET_FINGERPRINT`` before scoring with it.

The scoring matches MONAI's ``MedicalNetPerceptualSimilarity``: per-volume standardisation, the
final feature map only, L2-normalised across channels, squared difference summed over channels and
averaged over space. On the 96x112x80 grid that final map is 2048 x 3 x 4 x 3, so the loss scores
global structure rather than texture.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import torch
from monai.networks.nets import ResNetFeatures
from torch import nn

# The ResNet-50 the reference MSD brain recipe uses, downloaded by `make weights`
# (fl-tutorials/datasets/weights) under torch.hub's naming, `<arch>-<first 8 sha256 hex>.pth`.
MEDICALNET_ARCH = "resnet50"
MEDICALNET_CHECKPOINT = "medicalnet_resnet50_23datasets-ff48a622.pth"
MEDICALNET_SHA256_PREFIX = "ff48a622"
# What `make weights` converts it to, and config.json's SERVER_CHECKPOINT.
PERCEPTUAL_CHECKPOINT = "medicalnet_perceptual.pt"
PERCEPTUAL_PREFIX = "perceptual."
# weights_fingerprint() of the published weights: what every client must receive.
MEDICALNET_FINGERPRINT = "2c0c9d50290f6f351ec55d1305eb81a694ecb29cf2b8bd668d9bd7f3f362595d"  # pragma: allowlist secret

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


def build_backbone() -> nn.Module:
    """The bare MedicalNet backbone, frozen. Its weights arrive in the round-0 global model.

    Frozen here, once: the trainer only ever switches the autoencoder and discriminator to training
    mode, so the backbone's BatchNorm keeps its published statistics.
    """
    backbone = ResNetFeatures(MEDICALNET_ARCH, pretrained=False, spatial_dims=3, in_channels=1)
    backbone.eval()
    backbone.requires_grad_(False)
    return backbone


def weights_fingerprint(state_dict: Mapping[str, Any]) -> str:
    """SHA-256 over the backbone's weights: each tensor's name, dtype, shape and bytes, by name.

    Keys may carry the ``perceptual.`` prefix (the converted checkpoint) or not (the submodule's own
    ``state_dict()``); both spellings hash alike.
    """
    tensors = {key.removeprefix(PERCEPTUAL_PREFIX): torch.as_tensor(value) for key, value in state_dict.items()}
    digest = hashlib.sha256()
    for name in sorted(tensors):
        tensor = tensors[name].detach().cpu().contiguous()
        digest.update(f"{name}|{tensor.dtype}|{tuple(tensor.shape)}|".encode())
        digest.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def read_published_checkpoint(path: Path) -> dict[str, torch.Tensor]:
    """The published file's weights, keyed as the backbone's own ``state_dict()``.

    Raises:
        RuntimeError: if the file's hash is not the published one, or its keys do not cover the
            backbone exactly. A partial load would leave a randomly-initialised critic reporting
            plausible losses.
    """
    _verify_hash_prefix(path)
    state = torch.load(path, map_location="cpu", weights_only=True)
    # The published file wraps the tensors in a "state_dict" key and carries the `module.`
    # prefix left by the DataParallel wrapper it was trained under.
    state = state.get("state_dict", state)
    state = {key.removeprefix("module."): value for key, value in state.items()}
    missing, unexpected = build_backbone().load_state_dict(state, strict=False)
    if missing or unexpected:
        raise RuntimeError(
            f"{path.name} does not match MedicalNet {MEDICALNET_ARCH}: {len(missing)} missing and "
            f"{len(unexpected)} unexpected keys."
        )
    return state


def verify_backbone(backbone: nn.Module) -> None:
    """Raise unless the backbone holds the published MedicalNet weights.

    The server loads ``SERVER_CHECKPOINT`` with ``strict=False``, so a checkpoint it could not find
    leaves the backbone randomly initialised without an error of its own.
    """
    received = weights_fingerprint(backbone.state_dict())
    if received != MEDICALNET_FINGERPRINT:
        raise RuntimeError(
            f"The perceptual backbone is not MedicalNet {MEDICALNET_ARCH} (weights fingerprint "
            f"{received[:12]}…, expected {MEDICALNET_FINGERPRINT[:12]}…). Run `make weights` and upload "
            f"app_files/{PERCEPTUAL_CHECKPOINT} with the other app files: config.json names it "
            "SERVER_CHECKPOINT, and the FL server reads the weights from it (FLIP#1206)."
        )


class MedicalNetPerceptualLoss(nn.Module):
    """Perceptual distance between two 3-D volumes, measured by a frozen MedicalNet ResNet-50."""

    def __init__(self, backbone: nn.Module) -> None:
        super().__init__()
        self.model = backbone

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
