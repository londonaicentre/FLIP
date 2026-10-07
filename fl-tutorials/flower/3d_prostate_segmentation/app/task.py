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

"""The pieces of the nnU-Net training recipe the ClientApp assembles around ``train_seg``.

``nnunet_train.py`` (kept at the tutorial root as reference) set all of this up inline in its
``train_loop``: read the plan for the crop / patch / spacing / normalisation numbers, build the loss
with nnU-Net's ``DeepSupervisionWrapper``, an SGD optimiser and a polynomial LR schedule. This
module is that setup, factored so ``client_app.py`` reads like a recipe, plus the one function the
port did not have — a per-volume evaluation for the test split.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import torch
from einops import rearrange
from monai.inferers import SlidingWindowInferer
from monai.losses import DiceLoss
from monai.metrics import DiceMetric
from torch import nn
from torch.optim.lr_scheduler import PolynomialLR

from app.models import CONFIGURATION, deep_supervision_weights, set_deep_supervision_enabled
from app.train_helpers import possible_patch_size


@dataclass(frozen=True)
class PlanGeometry:
    """The numbers the plan fixes for preprocessing and patching, in the axis order each consumer wants.

    The plan stores spacing / shape / patch in nnU-Net's transposed ``(z, y, x)`` order. The MONAI
    loader and ``PatchIterd`` work on ``(x, y, z)`` volumes, so those get the reversed tuples; the
    network sees ``(z, y, x)`` after ``train_seg``'s rearrange, so the sliding-window ROI keeps the
    plan's order.
    """

    target_spacing: tuple[float, float, float]
    """Voxel spacing to resample every image to, (x, y, z) mm — ``dataset.build_loader``."""
    crop_size: tuple[int, int]
    """In-plane (x, y) pad/crop the whole volume to — ``preprocess.build_case_transform``."""
    patch_size: tuple[int, int, int]
    """Training patch, (x, y, z) — ``preprocess.build_patch_iter``."""
    patch_size_zyx: tuple[int, int, int]
    """The same patch in network order — the sliding-window ROI in ``evaluate_func``."""
    image_mean: float
    """Foreground intensity mean the plan measured — subtracted from every image."""
    image_std: float
    """Foreground intensity standard deviation — divides every image."""


def plan_geometry(plan: dict[str, Any], custom_patch: bool = False) -> PlanGeometry:
    """Read the crop / patch / spacing / normalisation numbers off the plan, as ``nnunet_train.py`` did.

    Args:
        plan: A parsed plan (``models.load_plan``).
        custom_patch: Use the largest in-plane patch that tiles the padded crop exactly
            (``possible_patch_size``) instead of the plan's own patch size.

    Returns:
        The geometry.
    """
    configuration = plan["configurations"][CONFIGURATION]
    intensity = plan["foreground_intensity_properties_per_channel"]["0"]
    median_shape_zyx = tuple(int(v) for v in plan["original_median_shape_after_transp"])
    spacing_zyx = tuple(float(v) for v in plan["original_median_spacing_after_transp"])
    plan_patch_zyx = tuple(int(v) for v in configuration["patch_size"])

    crop, candidate_patches = possible_patch_size(median_shape_zyx, plan_patch_zyx)
    if custom_patch:
        if not candidate_patches:
            raise ValueError(f"no candidate patch tiles the {crop[:2]} crop; use the plan's patch size instead")
        patch_xyz = tuple(int(v) for v in candidate_patches[-1])
    else:
        patch_xyz = plan_patch_zyx[::-1]

    return PlanGeometry(
        target_spacing=spacing_zyx[::-1],
        crop_size=(int(crop[0]), int(crop[1])),
        patch_size=patch_xyz,
        patch_size_zyx=patch_xyz[::-1],
        image_mean=float(intensity["mean"]),
        image_std=float(intensity["std"]),
    )


class DeepSupervisionLoss(nn.Module):
    """Weighted sum of one loss per network output — nnU-Net's ``DeepSupervisionWrapper``, in ten lines.

    The weights are renormalised over the outputs actually present in the call: in evaluation mode
    the network returns a single output, and without renormalising, that output's loss would be scaled by
    the first weight (about 0.5) and read lower than the same prediction scored in training.
    """

    def __init__(self, loss: nn.Module, weights: Sequence[float]) -> None:
        super().__init__()
        self.loss = loss
        self.weights = [float(w) for w in weights]

    def forward(self, preds: Sequence[torch.Tensor], targets: Sequence[torch.Tensor]) -> torch.Tensor:
        if len(preds) != len(targets):
            raise ValueError(f"{len(preds)} prediction(s) but {len(targets)} target(s)")
        weights = self.weights[: len(preds)]
        if len(weights) != len(preds):
            raise ValueError(f"{len(preds)} outputs but only {len(self.weights)} weights")
        total = sum(weights)
        return sum((w / total) * self.loss(p, t) for w, p, t in zip(weights, preds, targets, strict=True))


class DiceBCELoss(nn.Module):
    """Sigmoid soft Dice plus per-channel binary cross-entropy — nnU-Net's ``DC_and_BCE_loss`` for regions.

    The three label channels overlap (PZ ∪ TZ = whole gland), so each is its own binary problem and
    both terms treat it so. MONAI's ``DiceCELoss(sigmoid=True)`` does not: ``sigmoid`` reaches only
    its Dice term, and with more than one channel its CE term is a softmax ``CrossEntropyLoss`` over
    the channels, which is blind to every voxel where all three targets are 0 — the whole background.
    Trained with it, a network can mark the entire volume as gland at no CE cost, and did.

    Both terms are averaged (Dice per sample and channel, BCE per voxel) and summed with equal weight.
    """

    def __init__(self) -> None:
        super().__init__()
        self.dice = DiceLoss(include_background=True, sigmoid=True)
        self.bce = nn.BCEWithLogitsLoss()

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        return self.dice(logits, target) + self.bce(logits, target)


def build_criterion(conf: dict[str, Any], num_outputs: int) -> nn.Module:
    """``DiceBCELoss`` over the three overlapping label channels, wrapped for deep supervision.

    Sigmoid, not softmax: whole gland, PZ and TZ overlap (PZ ∪ TZ = whole gland), so each channel is
    its own binary problem.

    Args:
        conf: The ``TRAIN`` block of ``config.json``; reads ``deep_supervision``.
        num_outputs: How many outputs the network returns in training mode (``1 + deep_supr_num``).
    """
    loss = DiceBCELoss()
    if conf.get("deep_supervision", True):
        return DeepSupervisionLoss(loss, deep_supervision_weights(num_outputs))
    return loss


def build_optimizer(model: nn.Module, learning_rate: float, conf: dict[str, Any]) -> torch.optim.Optimizer:
    """nnU-Net's SGD: momentum 0.99, weight decay 3e-5 (``momentum`` / ``weight_decay`` in ``conf``)."""
    return torch.optim.SGD(
        (p for p in model.parameters() if p.requires_grad),
        lr=learning_rate,
        momentum=float(conf.get("momentum", 0.99)),
        weight_decay=float(conf.get("weight_decay", 3e-5)),
    )


def build_scheduler(
    optimizer: torch.optim.Optimizer, total_epochs: int, start_epoch: int, power: float = 0.9
) -> PolynomialLR:
    """nnU-Net's polynomial decay, spanning the WHOLE federated run.

    A client sees only its ``local-epochs`` of a round, so the schedule is fast-forwarded to where
    this round starts: ``total_epochs = num-server-rounds * local-epochs`` and ``start_epoch`` is the
    cumulative epoch count so far.
    """
    scheduler = PolynomialLR(optimizer, total_iters=total_epochs, power=power)
    with warnings.catch_warnings():
        # Stepping before the first optimizer.step() is exactly the point here.
        warnings.simplefilter("ignore")
        for _ in range(start_epoch):
            scheduler.step()
    return scheduler


def evaluate_func(
    model: nn.Module,
    loader: torch.utils.data.DataLoader,
    criterion: nn.Module,
    device: torch.device,
    roi_size_zyx: tuple[int, int, int],
) -> tuple[float, dict[str, float]]:
    """Score whole volumes with sliding-window inference — the test-split path.

    ``train_seg`` reports training and validation Dice per *patch*; this scores each volume in one
    piece, tiling it with ``roi_size_zyx`` windows and blending the overlaps, which is what a deployed
    model would do. Deep supervision is switched off for the pass and restored afterwards.

    Args:
        model: The network, already on ``device``.
        loader: Batches of ``{"image", "mask"}`` whole volumes in ``(B, C, x, y, z)`` layout.
        criterion: A plain (un-wrapped) loss.
        device: Where to run.
        roi_size_zyx: The sliding window, in the network's ``(z, y, x)`` order.

    Returns:
        ``(mean loss, {"dice_mean", "dice_wg", "dice_pz", "dice_tz"})``.
    """
    inferer = SlidingWindowInferer(roi_size=roi_size_zyx, sw_batch_size=2, overlap=0.5, mode="gaussian")
    dice_metric = DiceMetric(include_background=True, reduction="mean_batch")
    losses: list[float] = []

    was_training = model.training
    had_deep_supervision = bool(getattr(model, "deep_supervision", False))
    set_deep_supervision_enabled(False, network=model)
    model.eval()
    with torch.no_grad():
        for batch in loader:
            image = rearrange(batch["image"], "b c h w d -> b c d h w").float().to(device)
            mask = rearrange(batch["mask"], "b c h w d -> b c d h w").float().to(device)
            logits = inferer(image, model)
            losses.append(float(criterion(logits, mask).item()))
            dice_metric((torch.sigmoid(logits) > 0.5).float(), mask)
    set_deep_supervision_enabled(had_deep_supervision, network=model)
    if was_training:
        model.train()

    if not losses:
        return 0.0, {"dice_mean": 0.0, "dice_wg": 0.0, "dice_pz": 0.0, "dice_tz": 0.0}
    per_channel = dice_metric.aggregate()
    dice_metric.reset()
    wg, pz, tz = (float(v) for v in per_channel)
    return sum(losses) / len(losses), {"dice_mean": (wg + pz + tz) / 3, "dice_wg": wg, "dice_pz": pz, "dice_tz": tz}
