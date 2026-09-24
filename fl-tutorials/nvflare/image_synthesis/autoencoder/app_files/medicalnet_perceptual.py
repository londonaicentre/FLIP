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

"""A 3-D MedicalNet perceptual loss that never reaches the network.

MONAI ships this loss as ``PerceptualLoss(spatial_dims=3, network_type="medicalnet_resnet10_23datasets")``,
but that constructor is unusable in a FLIP app: it builds its backbone with
``torch.hub.load("Project-MONAI/perceptual-models:main", ...)``, which fetches a **GitHub repository
and then a checkpoint at run time**. FL apps never download at run time (FLIP#1206) — a platform FL
server has no internet route, and a run-time fetch bypasses the scanned upload path — and
``torch.hub.load`` is on the offline guard's denylist for exactly that reason. Staging the cache the
way the SqueezeNet backbone is staged does not help either, because what torch.hub needs cached is a
whole *repository directory*, and the upload path and job bundler are flat.

So this module builds the same network from MONAI's own ``ResNetFeatures`` — which takes an
architecture name and no URL — and loads a checkpoint that ships with the app like any other
uploaded weight file. The architecture matches bit for bit: ``resnet10`` is ``BasicBlock`` with
layers ``[1, 1, 1, 1]``, ``shortcut_type="B"``, ``bias_downsample=False`` and no classifier head, and
the published MedicalNet state dict loads into it with **zero missing and zero unexpected keys**
(asserted at load time below, because a silent partial load would leave a randomly-initialised
"pretrained" critic that still produces plausible-looking loss values).

**The scoring reproduces ``MedicalNetPerceptualSimilarity`` exactly**, deliberately: per-volume
intensity standardisation, one forward pass per input channel, the **final** feature map only,
L2-normalisation across channels, squared difference summed over channels and averaged over space.
``ResNetFeatures`` exposes all five scales; MONAI's hub network returns only its ``layer4`` output,
so this takes ``[-1]`` to match. For a 96-cubed volume that final map is ``512 x 3 x 3 x 3`` — 27
spatial positions, i.e. a global-structure signal rather than a texture one. That is a real
limitation of the reference loss, not an accident of this port; using the earlier, higher-resolution
maps as well would score fine detail, but would no longer be the MONAI loss.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import torch
from monai.networks.nets import ResNetFeatures
from torch import nn

logger = logging.getLogger(__name__)

# The shipped checkpoint, named the way torch.hub names weights: `<arch>-<first 8 sha256 hex>.pth`.
# `make weights` in the tutorial directory stages it; see fl-tutorials/datasets/weights.
#
# ``resnet50`` is what the reference MSD brain-tumour recipe this tutorial is modelled on uses. The
# deeper network is a stronger critic (2048 channels at the final map against ResNet-10's 512) at
# the cost of a 185 MB upload per job instead of 57 MB. ``resnet10`` stays wired up and tested so
# the trade can be made by editing one constant.
MEDICALNET_CHECKPOINTS = {
    "resnet10": ("medicalnet_resnet10_23datasets-afa8055f.pth", "afa8055f"),
    "resnet50": ("medicalnet_resnet50_23datasets-ff48a622.pth", "ff48a622"),
}
MEDICALNET_ARCH = "resnet50"
MEDICALNET_CHECKPOINT, MEDICALNET_SHA256_PREFIX = MEDICALNET_CHECKPOINTS[MEDICALNET_ARCH]

# Guard epsilons. **float32 only**: 1e-10 is exactly zero in float16, so adding it inside an
# ``autocast`` region guards nothing — the very case it exists for (a zero denominator) still
# divides by zero. Every expression using it below therefore casts to float32 first.
_EPS = 1e-10


def _intensity_normalisation(volume: torch.Tensor) -> torch.Tensor:
    """Standardise a whole volume to zero mean and unit variance.

    MedicalNet was trained on volumes preprocessed this way, so the features are only meaningful on
    inputs in the same range. Matches ``monai.losses.perceptual.medicalnet_intensity_normalisation``
    apart from the ``_EPS`` in the denominator, which is a deliberate departure: the reference divides
    by a bare ``std()``, so a **constant** volume yields ``0/0 = NaN``. An undertrained decoder really
    does emit near-constant output, and the training loop treats any NaN as fatal — so a loss that can
    manufacture one is a liveness bug, not a theoretical edge case. The numerator is exactly zero in
    that case, so the guard maps a constant volume to zeros and changes nothing for real inputs
    (brain volumes have ``std`` of order 0.1, against an epsilon of 1e-10).
    """
    volume = volume.float()
    return (volume - volume.mean()) / (volume.std() + _EPS)


def _normalize_tensor(x: torch.Tensor) -> torch.Tensor:
    """L2-normalise across the channel axis, as the reference implementation does.

    Computed in float32 even when the caller is inside ``autocast``. Two reasons, both live: the
    sum of squares runs over up to 2048 channels and overflows float16 well before float32, and the
    ``_EPS`` that keeps an all-zero position (every channel dead after ReLU) from dividing by zero
    is itself zero in float16.
    """
    x = x.float()
    norm_factor = torch.sqrt(torch.sum(x**2, dim=1, keepdim=True))
    return x / (norm_factor + _EPS)


def _verify_hash_prefix(path: Path) -> None:
    """Raise unless ``path``'s sha256 starts with the prefix embedded in its filename.

    The file is read back rather than trusted: torch reuses a checkpoint already on disk without
    re-checking it, so an interrupted or swapped download would otherwise be loaded silently.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith(MEDICALNET_SHA256_PREFIX):
        raise RuntimeError(
            f"{path} has sha256 {digest[: len(MEDICALNET_SHA256_PREFIX)]}…, expected "
            f"{MEDICALNET_SHA256_PREFIX}…; delete it and re-run `make weights` (FLIP#1206)."
        )


class MedicalNetPerceptualLoss(nn.Module):
    """Perceptual distance between two 3-D volumes, measured by a frozen MedicalNet ResNet-10.

    Carries ``spatial_dims = 3`` so callers can tell it apart from the 2-D losses, which have to be
    fed individual slices; this one takes the volume whole and therefore sees anatomy that a slice
    sampler would miss.
    """

    spatial_dims = 3

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

        # MedicalNet takes one channel at a time; multi-channel input is scored channel by channel
        # and the features concatenated, exactly as the reference does.
        feats_input = torch.cat(
            [self.model(input[:, idx, ...].unsqueeze(1))[-1] for idx in range(input.shape[1])], dim=1
        )
        feats_target = torch.cat(
            [self.model(target[:, idx, ...].unsqueeze(1))[-1] for idx in range(target.shape[1])], dim=1
        )

        difference = (_normalize_tensor(feats_input) - _normalize_tensor(feats_target)) ** 2
        return difference.sum(dim=1, keepdim=True).mean([2, 3, 4], keepdim=True).mean()


def load_medicalnet_perceptual(working_dir: Path) -> MedicalNetPerceptualLoss:
    """Build the loss from the checkpoint shipped beside the training script.

    Uploads and the job bundler are flat, so the checkpoint arrives as
    ``<app dir>/<MEDICALNET_CHECKPOINT>``. Missing means the app was uploaded without it — which is a
    hard error rather than a fetch, because fetching is the thing FLIP#1206 forbids.
    """
    shipped = working_dir / MEDICALNET_CHECKPOINT
    if not shipped.is_file():
        raise FileNotFoundError(
            f"{MEDICALNET_CHECKPOINT} is missing from {working_dir}. FL apps do not download weights at "
            "run time (FLIP#1206): run `make weights` in the tutorial directory to stage it, and upload "
            "it together with the other app files."
        )
    _verify_hash_prefix(shipped)
    logger.info(f"MedicalNet perceptual backbone loaded from {shipped.name} (frozen, 3-D, final feature map).")
    return MedicalNetPerceptualLoss(shipped)
