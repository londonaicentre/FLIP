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
# Adapted from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/train_helpers.py

import gc
import glob
import inspect
import math
import os
import random
import warnings
from contextlib import nullcontext
from logging import DEBUG, FileHandler, Formatter, Logger, StreamHandler, getLogger
from typing import Any

import nibabel as nib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from monai.data import MetaTensor
from monai.metrics import (
    DiceMetric,
    HausdorffDistanceMetric,
    compute_dice,
    compute_hausdorff_distance,
)
from monai.transforms import Activationsd, Compose, Invertd
from scipy import stats
from tqdm.auto import tqdm

from dataset import IMAGE_KEY
from network import set_deep_supervision_enabled


class AverageMeter:
    """Computes and stores the average and current value, ignoring NaN updates.

    Moved here from MambaX-Net's metrics/utils.py (flattened as metrics_utils.py), train_seg being
    its only consumer. monai.metrics.CumulativeAverage is NOT a drop-in: it has no NaN-skipping
    branch and its aggregate() performs a distributed all-gather.

    Note reset() allocates on .cuda(), so train_seg requires a GPU — upstream behaviour, preserved.

    Attributes:
        val (torch.Tensor): Most recently added value (a tensor at reset, a Python float after update() is called).
        avg (torch.Tensor): Running average of all values added so far, excluding NaNs (a tensor
            at reset, a Python float after update() is called).
        sum (torch.Tensor): Running sum of all values added so far, excluding NaNs.
        count (torch.Tensor): Running count of samples represented by non-NaN updates.
        nan_count (torch.Tensor): Number of times update() was called with a NaN value.
    """

    def __init__(self) -> None:
        """Computes and stores the average and current value."""
        self.reset()

    def reset(self) -> None:
        """Resets all statistics."""
        self.val = torch.tensor(0.0).cuda()
        self.avg = torch.tensor(0.0).cuda()
        self.sum = torch.tensor(0.0).cuda()
        self.count = torch.tensor(0).cuda()
        self.nan_count = torch.tensor(0).cuda()

    def update(self, val: torch.Tensor, n: int = 1) -> None:
        """Updates the meter with the new value.

        Args:
            val (torch.Tensor): The new value to add.
            n (int, optional): The number of samples represented by the new value. Defaults to 1.
        """
        val = val.item()
        self.val = val

        if math.isnan(val):
            self.nan_count += 1

        # sum only if the value is not NaN
        if not math.isnan(val):
            self.sum += val * n
            self.count += n

        if self.count != 0:
            self.avg = (self.sum / self.count).detach().cpu().item()
        else:
            self.avg = torch.tensor(0.0).detach().cpu().item()


def init_logger(log_file: str = "train.log") -> Logger:
    """Initialize the logger.

    Args:
        log_file (str, optional): Path to the log file. Defaults to "train.log".

    Returns:
        Logger: The configured logger instance.
    """
    log_format = "%(asctime)s %(levelname)s %(message)s"

    stream_handler = StreamHandler()
    stream_handler.setLevel(DEBUG)
    stream_handler.setFormatter(Formatter(log_format))

    file_handler = FileHandler(log_file)
    file_handler.setFormatter(Formatter(log_format))

    logger = getLogger("PCa")
    logger.setLevel(DEBUG)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    return logger


def seed_torch(seed: int = 42) -> None:
    """Set the random seed for reproducibility.

    Args:
        seed (int, optional): The random seed. Defaults to 42.
    """
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def possible_patch_size(
    image_size: tuple[int, int, int],
    suggested_patch_sz: tuple[int, int, int],
    patch_sizes: tuple[int, ...] = (64, 128, 256, 512),
) -> tuple[tuple[int, int, int], list[tuple[int, int, int]]]:
    """Determines a padded crop size and the candidate patch sizes it fits

    image_size[1] (the in-plane dimension) is padded up to the next even
    number to give the crop's x/y extent, and each candidate in patch_sizes
    that evenly divides that extent is kept as a possible in-plane patch
    size, paired with the suggested z patch size.

    Moved here from MambaX-Net's nifti_utilities.py, the only function of that module this
    tutorial ever used — the rest was NIfTI load/save superseded by dataset.py's MONAI
    LoadImaged/Orientationd chain. Callers take crop_sz[0:2] and patch_sz[-1]; crop_sz[2]
    (suggested z + 8) is returned for interface parity and read by no one.

    Args:
        image_size (tuple[int, int, int]): size of the image
        suggested_patch_sz (tuple[int, int, int]): suggested patch size,
            whose first element (z) is used
        patch_sizes (tuple[int, ...]): candidate in-plane patch sizes to test

    Returns:
        crop_size (tuple[int, int, int]): padded (x, y, z) crop size, with
            x/y rounded up to even and z equal to suggested z patch size + 8
        sizes (list[tuple[int, int, int]]): (patch_size, patch_size, z)
            tuples for each candidate patch size that evenly divides the
            padded x/y extent
    """
    x_remainder = image_size[1] % 2
    _ = image_size[0] % 2

    suggested_z_patch = suggested_patch_sz[0]

    new_x = int(image_size[1] + x_remainder)
    new_y = new_x
    new_z = suggested_z_patch

    sizes = []
    for patch_size in patch_sizes:
        if new_x % patch_size == 0:
            sizes.append((patch_size, patch_size, new_z))
    return (new_x, new_y, suggested_z_patch + 8), sizes


def train_seg(
    conf: dict[str, Any],
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    criterion: nn.Module,
    device: torch.device,
    dual_scan: bool = False,
):
    """Run one training epoch followed by validation for a segmentation model.

    Iterates over train_loader, computes the (optionally deep-supervised) loss via
    criterion, backpropagates with optional bf16 autocast, steps the OneCycle
    scheduler per batch if configured, tracks running Dice metrics (overall/WP/PZ/TZ),
    and tracks per-batch metrics. Then switches the model to
    eval mode and repeats the same forward/metric computation (without backprop) over
    val_loader. Supports dual-scan inputs (mask split into current and
    previous-scan channels) and channels_last_3d memory format when the model's
    parameters are already in that format.

    Args:
        conf (Dict[str, Any]): Training configuration (bf16, deep_supervision, max_norm,
            norm_type, scheduler, etc.).
        model (nn.Module): Segmentation model being trained.
        optimizer (torch.optim.Optimizer): Optimizer for the model parameters.
        scheduler (torch.optim.lr_scheduler._LRScheduler): LR scheduler; stepped per
            batch only when conf["scheduler"] == "OneCycle".
        train_loader (torch.utils.data.DataLoader): Training loader yielding
            {"image", "mask", "accession_id"} batches (monai.data.list_data_collate).
        val_loader (torch.utils.data.DataLoader): Validation loader with the same batch format.
        criterion (nn.Module): Loss function; called with a list of predictions/targets
            when deep supervision is enabled.
        device (torch.device): Device to move batches to.
        dual_scan (bool, optional): If True, splits mask into current and previous-scan
            channels and passes the previous scan mask to the model. Defaults to False.

    Returns:
        Tuple[float, float, Dict[str, float]]: Average training loss, average
        validation loss, and a dict of averaged train/val Dice metrics
        (e.g. "train/mean_dice_avg", "val/wp_dice_avg", ...).
    """

    def _build_ds_targets(
        target_fullres: torch.Tensor,
        logits_list: list[torch.Tensor] | tuple[torch.Tensor, ...],
    ) -> list[torch.Tensor]:
        """Build one target per deep supervision output.

        DeepSupervisionWrapper expects a list of targets with the same length as
        the list of model outputs. We resize the full-resolution target to each
        output's spatial size using nearest-neighbor interpolation.
        """

        targets: list[torch.Tensor] = []
        with torch.no_grad():
            for pred in logits_list:
                if not isinstance(pred, torch.Tensor) or pred.ndim < 3:
                    targets.append(target_fullres)
                    continue

                # Expect [B, C, *spatial] for both prediction and target.
                if pred.ndim != target_fullres.ndim:
                    targets.append(target_fullres)
                    continue

                pred_spatial = tuple(pred.shape[2:])
                tgt_spatial = tuple(target_fullres.shape[2:])
                if pred_spatial == tgt_spatial:
                    targets.append(target_fullres)
                    continue

                resized = F.interpolate(
                    target_fullres.float(),
                    size=pred_spatial,
                    mode="nearest",
                )
                targets.append(resized.to(dtype=target_fullres.dtype))

        return targets

    model.train()

    # Detect if model uses channels_last_3d (e.g. SwinUNETR, SegMamba)
    _use_cl3d = any(
        p.is_contiguous(memory_format=torch.channels_last_3d)
        for p in model.parameters()
        if p.ndim == 5
    )

    train_loss = AverageMeter()
    train_mean_dice = AverageMeter()
    train_wp_dice = AverageMeter()
    train_pz_dice = AverageMeter()
    train_tz_dice = AverageMeter()

    train_dice_metric = DiceMetric(include_background=True, reduction="mean")
    train_dice_metric_batch = DiceMetric(
        include_background=True, reduction="mean_batch"
    )

    train_bar = tqdm(train_loader, total=len(train_loader))
    for batch in train_bar:
        image, mask = batch["image"], batch["mask"]
        if dual_scan:
            mask, mask_prev = mask[:, :3, ...], mask[:, 3:, ...]

            image, mask, mask_prev = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask_prev, "b c h w d -> b c d h w").float().to(device),
            )
        else:
            image, mask = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
            )

        if _use_cl3d:
            image = image.contiguous(memory_format=torch.channels_last_3d)
            mask = mask.contiguous(memory_format=torch.channels_last_3d)
            if dual_scan:
                mask_prev = mask_prev.contiguous(memory_format=torch.channels_last_3d)

        optimizer.zero_grad(set_to_none=True)

        amp_context = (
            torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16)
            if conf.get("bf16", True)
            else nullcontext()
        )
        with amp_context:
            if dual_scan:
                logits = model(image, mask_prev)
            else:
                logits = model(image)

            if conf.get("deep_supervision", True):
                pred_list = logits if isinstance(logits, (list, tuple)) else [logits]
                loss = criterion(pred_list, _build_ds_targets(mask, pred_list))
                logits = pred_list[0]
            else:
                loss = criterion(logits, mask)

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        train_dice_metric(logits.detach(), mask.detach())
        train_dice_metric_batch(logits.detach(), mask.detach())

        loss.backward()
        if conf.get("max_norm", 12) is not None:
            torch.nn.utils.clip_grad_norm_(
                model.parameters(), conf.get("max_norm", 12), conf.get("norm_type", 2)
            )
        optimizer.step()

        if conf.get("scheduler", "Polynomial") == "OneCycle":
            scheduler.step()

        train_loss.update(loss.detach())

        # Per-channel order is [whole_gland, pz, tz], matching dataset.py's combine_masks.
        train_metric = train_dice_metric.aggregate()
        train_metric_wp, train_metric_pz, train_metric_tz = (
            train_dice_metric_batch.aggregate().detach()
        )
        train_dice_metric.reset()
        train_dice_metric_batch.reset()
        train_mean_dice.update(train_metric)
        train_wp_dice.update(train_metric_wp)
        train_pz_dice.update(train_metric_pz)
        train_tz_dice.update(train_metric_tz)

        train_bar.set_description(f"average train loss: {train_loss.avg:.5f}")

    model.eval()

    val_loss = AverageMeter()
    val_mean_dice = AverageMeter()
    val_wp_dice = AverageMeter()
    val_pz_dice = AverageMeter()
    val_tz_dice = AverageMeter()

    val_dice_metric = DiceMetric(include_background=True, reduction="mean")
    val_dice_metric_batch = DiceMetric(include_background=True, reduction="mean_batch")

    val_bar = tqdm(val_loader, total=len(val_loader))
    for batch in val_bar:
        image, mask = batch["image"], batch["mask"]
        if dual_scan:
            mask, mask_prev = mask[:, :3, ...], mask[:, 3:, ...]

            image, mask, mask_prev = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask_prev, "b c h w d -> b c d h w").float().to(device),
            )
        else:
            image, mask = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
            )

        if _use_cl3d:
            image = image.contiguous(memory_format=torch.channels_last_3d)
            mask = mask.contiguous(memory_format=torch.channels_last_3d)
            if dual_scan:
                mask_prev = mask_prev.contiguous(memory_format=torch.channels_last_3d)

        with torch.no_grad():
            if dual_scan:
                logits = model(image, mask_prev)
            else:
                logits = model(image)

            if conf.get("deep_supervision", True):
                pred_list = logits if isinstance(logits, (list, tuple)) else [logits]
                loss = criterion(pred_list, _build_ds_targets(mask, pred_list))
                logits = pred_list[0]
            else:
                loss = criterion(logits, mask)

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        val_dice_metric(logits.detach(), mask.detach())
        val_dice_metric_batch(logits.detach(), mask.detach())

        val_metric = val_dice_metric.aggregate()
        val_metric_wp, val_metric_pz, val_metric_tz = (
            val_dice_metric_batch.aggregate().detach()
        )
        val_dice_metric.reset()
        val_dice_metric_batch.reset()

        val_loss.update(loss.detach())
        val_mean_dice.update(val_metric)
        val_wp_dice.update(val_metric_wp)
        val_pz_dice.update(val_metric_pz)
        val_tz_dice.update(val_metric_tz)

        val_bar.set_description(f"average val loss: {val_loss.avg:.5f}")

    torch.cuda.empty_cache()
    gc.collect()
    metrics = {
        "train/mean_dice_avg": train_mean_dice.avg,
        "train/wp_dice_avg": train_wp_dice.avg,
        "train/pz_dice_avg": train_pz_dice.avg,
        "train/tz_dice_avg": train_tz_dice.avg,
        "val/mean_dice_avg": val_mean_dice.avg,
        "val/wp_dice_avg": val_wp_dice.avg,
        "val/pz_dice_avg": val_pz_dice.avg,
        "val/tz_dice_avg": val_tz_dice.avg,
    }

    return train_loss.avg, val_loss.avg, metrics


# TTA flip axes (spatial dims of BCDHW tensors)
_TTA_FLIP_DIMS: list[list[int]] = [[2], [3], [4]]


def _forward_with_tta_flag(
    models: list,
    image: torch.Tensor,
    mask: torch.Tensor,
    dual_scan: bool,
    deep_supervision: bool,
    use_tta: bool = False,
    mask_prev: torch.Tensor | None = None,
) -> torch.Tensor:
    """Average raw logits across folds, optionally over 3-axis TTA flips.

    With use_tta, runs 4 forward passes per fold (original + flip-D + flip-H + flip-W)
    and un-flips each output before averaging; without it, the single un-flipped view.

    The no-TTA case used to be a separate _plain_ensemble_forward. It was exactly this
    loop over the one `None` view — verified identical over every branch (1/2-channel
    input, 1-3 folds, dual_scan, deep_supervision, and the `mask`-in-signature path) —
    so the two were merged rather than kept in sync by hand.
    """
    all_logits: list[torch.Tensor] = []
    tta_views = ([None] + _TTA_FLIP_DIMS) if use_tta else [None]

    for flip_dims in tta_views:
        img_f = torch.flip(image, flip_dims) if flip_dims is not None else image
        mprev_f = (
            torch.flip(mask_prev, flip_dims)
            if (flip_dims is not None and mask_prev is not None)
            else mask_prev
        )

        for model in models:
            if dual_scan:
                logits = model(img_f, mprev_f)
            else:
                sig = inspect.signature(model.forward)
                if "mask" in sig.parameters:
                    img_d = (
                        torch.cat([img_f, img_f], dim=1)
                        if img_f.shape[1] == 1
                        else img_f
                    )
                    logits = model(img_d, mask)
                else:
                    logits = model(img_f)

            if deep_supervision:
                logits = logits[0]

            if flip_dims is not None:
                logits = torch.flip(logits, flip_dims)

            all_logits.append(logits)

    return torch.mean(torch.stack(all_logits), dim=0)


# Keys for gland-region metrics (used by inference_func + callers) ──
_REGION_METRIC_KEYS: list[str] = [
    f"{r}_{z}_{m}"
    for r in ("apex", "mid", "base")
    for z in ("wp", "pz", "tz")
    for m in ("dice", "hdf")
]


def _gland_region_metrics(
    logits: torch.Tensor,
    masks: torch.Tensor,
    spacing: tuple[float, float, float] = (3.0, 0.5, 0.5),
) -> dict:
    """Compute WP/PZ/TZ DSC and HD95 for apex, mid-gland, and base thirds.

    Divides the WP-occupied axial slices into three equal thirds and computes per-region Dice
    (monai.metrics.compute_dice) and HD95 (monai.metrics.compute_hausdorff_distance) per subject.
    NaN is returned when a region has < 3 occupied slices; MONAI's own ignore_empty/percentile
    handling returns NaN when prediction and/or GT are empty in that region too (verified to match
    this function's previous hand-rolled numpy/scipy behaviour: Dice NaN only when both sides are
    empty, HD95 NaN when either side is empty).

    Args:
        logits: (B, C, D, H, W) tensor, thresholded binary predictions.
        masks: (B, C, D, H, W) tensor, ground-truth binary masks. Channel layout: 0=WP, 1=PZ, 2=TZ.
        spacing: (D, H, W) voxel spacing in mm, used for the HD95 distance computation. Must match
            the spacing the volumes were actually resampled to (nnunet_train.py's/nnunet_infer.py's
            `spacing` from the nnU-Net plans) — the (3.0, 0.5, 0.5) default is only correct if that
            happens to be the plan's spacing.

    Returns:
        Dict with keys ``{region}_{zone}_{metric}``; each value is a list of per-subject floats.
    """
    accum: dict = {k: [] for k in _REGION_METRIC_KEYS}
    zones = (("wp", 0), ("pz", 1), ("tz", 2))

    for i in range(logits.shape[0]):
        pred = logits[i]  # (C, D, H, W)
        gt = masks[i]

        # Find WP-occupied slices along the D axis (channel 0 = WP)
        wp_occ = gt[0].sum(dim=(-2, -1)) > 0  # (D,)
        occ_idx = torch.where(wp_occ)[0]

        if len(occ_idx) < 3:
            for k in _REGION_METRIC_KEYS:
                accum[k].append(float("nan"))
            continue

        n = len(occ_idx)
        t1, t2 = n // 3, 2 * n // 3
        regions = {
            "apex": occ_idx[:t1],
            "mid": occ_idx[t1:t2],
            "base": occ_idx[t2:],
        }

        for region, sl_idxs in regions.items():
            p_vol = pred[:, sl_idxs].unsqueeze(0)  # (1, C, n_sl, H, W)
            g_vol = gt[:, sl_idxs].unsqueeze(0)

            with warnings.catch_warnings():
                # Routine here — a region third with no voxels for a given zone is expected, not
                # an error; compute_dice/compute_hausdorff_distance already return NaN for it.
                warnings.simplefilter("ignore", category=UserWarning)
                dsc = compute_dice(p_vol, g_vol, include_background=True).squeeze(
                    0
                )  # (C,)
                hdf = compute_hausdorff_distance(
                    p_vol,
                    g_vol,
                    include_background=True,
                    percentile=95,
                    spacing=spacing,
                ).squeeze(0)

            for zone, ch in zones:
                accum[f"{region}_{zone}_dice"].append(float(dsc[ch]))
                accum[f"{region}_{zone}_hdf"].append(float(hdf[ch]))

    return accum


def _ci95_from_values(values: np.ndarray | list[float]) -> float:
    """95% CI half-width for the mean of subject-level holdout metrics."""
    finite_values = np.asarray(values, dtype=float)
    finite_values = finite_values[~np.isnan(finite_values)]
    n_values = finite_values.size
    if n_values <= 1:
        return float("nan")

    sample_std = float(np.std(finite_values, ddof=1))
    return float(stats.t.ppf(0.975, df=n_values - 1) * sample_std / np.sqrt(n_values))


def _ci95_bounds(mean: float, ci95: float) -> tuple[float, float]:
    if not (np.isfinite(mean) and np.isfinite(ci95)):
        return float("nan"), float("nan")
    return mean - ci95, mean + ci95


def _group_patches_by_case(names: list[str]) -> tuple[list[str], list[int]]:
    """Collapse a flattened per-patch accession_id list into (case names, patches per case).

    `monai.data.list_data_collate` flattens each case's patch list into the batch, so a batch
    of B cases arrives as one long run of patches with the accession_id repeated. Batching
    happens at the CASE level, so a case's patches are always contiguous and never split
    across two batches — verified for batch_size 1..3 with uneven patch counts. This is the
    `patches_per_image` MambaX-Net's patch_collate_infer counted with `len(sublist)`.
    """
    case_names: list[str] = []
    counts: list[int] = []
    for name in names:
        if case_names and case_names[-1] == name:
            counts[-1] += 1
        else:
            case_names.append(name)
            counts.append(1)
    return case_names, counts


def _per_case_shape(collated_shape: list, counts: list[int]) -> list[tuple[int, ...]]:
    """Pull one volume shape per case out of list_data_collate's per-axis tensors.

    A tuple value collates to a list of per-axis tensors, each of batch length, rather than to
    one tensor per item — so the shape of the case starting at `idx` is read down that column.
    """
    shapes: list[tuple[int, ...]] = []
    idx = 0
    for count in counts:
        shapes.append(tuple(int(axis[idx]) for axis in collated_shape))
        idx += count
    return shapes


def _write_patch(
    volume: torch.Tensor,
    patch: torch.Tensor,
    coord: tuple[list[int], list[int], list[int], list[int]],
) -> None:
    """Copy a patch into volume at the location given by coord, in place.

    Clips the patch to volume's bounds along each spatial axis and does nothing
    if the resulting overlap is empty (non-positive extent) in any dimension.

    Args:
        volume (torch.Tensor): Destination tensor of shape [C, H, W, D]; modified in place.
        patch (torch.Tensor): Source patch tensor of shape [C, h, w, d].
        coord (Tuple[List[int], List[int], List[int], List[int]]): Patch coordinates as
            (c_coord, h_coord, w_coord, d_coord), where h_coord/w_coord/d_coord are each
            a two-element [start, end] range in volume space.
    """
    _, h_coord, w_coord, d_coord = coord
    h0, h1 = int(h_coord[0]), int(h_coord[1])
    w0, w1 = int(w_coord[0]), int(w_coord[1])
    d0, d1 = int(d_coord[0]), int(d_coord[1])

    h1 = min(h1, volume.shape[1])
    w1 = min(w1, volume.shape[2])
    d1 = min(d1, volume.shape[3])

    patch_h = h1 - h0
    patch_w = w1 - w0
    patch_d = d1 - d0
    if patch_h <= 0 or patch_w <= 0 or patch_d <= 0:
        return

    volume[:, h0:h1, w0:w1, d0:d1] = patch[:, :patch_h, :patch_w, :patch_d]


def recreate_image(
    batch_sz: int,
    logits: torch.Tensor,
    probs: torch.Tensor,
    img_patches: torch.Tensor,
    mask_patches: torch.Tensor,
    img_size: list[tuple[int, ...]],
    mask_size: list[tuple[int, ...]],
    coords: list[tuple[list[int], list[int], list[int], list[int]]],
    patches_per_image: list[int] | None = None,
) -> tuple[
    list[torch.Tensor], list[torch.Tensor], list[torch.Tensor], list[torch.Tensor]
]:
    """
    Recreate full-size images from patches by assembling them according to their coordinates.

    This function takes patches of images, masks, logits, and probabilities along with their
    coordinate information and reconstructs the original full-size tensors. It handles
    overlapping patches and boundary conditions for medical image segmentation tasks.

    Args:
        batch_sz (int): Original batch size before patch extraction
        logits (torch.Tensor): Predicted logits from model of shape [N, C, H, W, D]
        probs (torch.Tensor): Predicted probabilities from model of shape [N, C, H, W, D]
        img_patches (torch.Tensor): Tensor containing all image patches
        mask_patches (torch.Tensor): Tensor containing all mask patches
        img_size (List[Tuple[int, ...]]): Original image sizes for each item in batch
        mask_size (List[Tuple[int, ...]]): Original mask sizes for each item in batch
        coords (List[Tuple[List[int], List[int], List[int], List[int]]]):
            Patch coordinates as (c_coord, h_coord, w_coord, d_coord) for each patch
        patches_per_image (Optional[List[int]]): Number of patches for each image in batch.
            If None, assumes uniform distribution (legacy behavior).

    Returns:
        Tuple[List[torch.Tensor], List[torch.Tensor], List[torch.Tensor], List[torch.Tensor]]:
            A tuple containing:
            - new_logits: List of reconstructed logit tensors
            - new_probabilities: List of reconstructed probability tensors
            - new_images: List of reconstructed image tensors
            - new_masks: List of reconstructed mask tensors

    Note:
        This function handles variable numbers of patches per image when patches_per_image
        is provided, enabling batch_size > 1 with different patch counts per image.
    """
    true_batch_sz: int = logits.shape[0]

    # Calculate patch indices for each image
    if patches_per_image is None:
        # Legacy behavior: assume uniform distribution
        multiplier: int = true_batch_sz // batch_sz
        patch_indices = [
            (i * multiplier, (i + 1) * multiplier) for i in range(batch_sz)
        ]
    else:
        # Variable patches per image
        patch_indices = []
        start_idx = 0
        for count in patches_per_image:
            patch_indices.append((start_idx, start_idx + count))
            start_idx += count

    new_images, new_masks, new_logits, new_probabilities = [], [], [], []
    for i, (im_sz, m_sz) in enumerate(zip(img_size, mask_size)):
        # create empty image and mask
        new_img: torch.Tensor = torch.zeros(im_sz, dtype=img_patches.dtype)
        new_mask: torch.Tensor = torch.zeros(m_sz, dtype=mask_patches.dtype)
        new_logit: torch.Tensor = torch.zeros(m_sz, dtype=logits.dtype)
        new_probs: torch.Tensor = torch.zeros(m_sz, dtype=probs.dtype)

        # Get patches for this image using calculated indices
        start_idx, end_idx = patch_indices[i]

        # Skip images with no patches
        if start_idx == end_idx:
            print(f"Warning: Image {i} has no patches, skipping reconstruction")
            new_images.append(new_img)
            new_masks.append(new_mask)
            new_logits.append(new_logit)
            new_probabilities.append(new_probs)
            continue

        img = img_patches[start_idx:end_idx]
        mask = mask_patches[start_idx:end_idx]
        logits_ = logits[start_idx:end_idx]
        probs_ = probs[start_idx:end_idx]
        coords_ = coords[start_idx:end_idx]

        for logit, prob, img_patch, mask_patch, coord in zip(
            logits_, probs_, img, mask, coords_
        ):
            _write_patch(new_img, img_patch, coord)
            _write_patch(new_mask, mask_patch, coord)
            _write_patch(new_logit, logit, coord)
            _write_patch(new_probs, prob, coord)

        new_images.append(new_img)
        new_masks.append(new_mask)
        new_logits.append(new_logit)
        new_probabilities.append(new_probs)
    return new_logits, new_probabilities, new_images, new_masks


def inference_func(
    conf: dict[str, Any],
    exp_name: str,
    model_path: str,
    model: nn.Module,
    data_loader: torch.utils.data.DataLoader,
    device: torch.device,
    criterion: nn.Module,
    dual_scan: bool = False,
    use_tta: bool = False,
    spacing: tuple[float, float, float] = (3.0, 0.5, 0.5),
) -> dict[str, Any]:
    """Run inference/evaluation over data_loader with a fold ensemble and report test metrics.

    Loads one model checkpoint per file matching "{model_path}/{exp_name}_*.pt" into
    an ensemble, then for each batch: forwards through the ensemble (optionally with
    3-axis TTA flip averaging via _forward_with_tta_flag), computes the loss,
    thresholds logits into binary predictions, and accumulates Dice, HD95, and
    per-region (apex/mid/base) WP/PZ/TZ metrics. Saves a per-subject bootstrap score
    table to "{exp_name}_bootstrap.csv". Finally computes means, stds, and 95% CI
    bounds (overall and per gland region) for all metrics.

    Unlike MambaX-Net's original (patch-iterator based) version, data_loader is expected to yield
    the whole-volume `{"image", "mask", "accession_id"}` batches dataset.py/data_loading.py already
    build (monai.data.list_data_collate) — each batch item is one full preprocessed case, never a
    patch, so there is no patch reconstruction step here.

    Args:
        conf: Config dict/object with keys "bf16", "deep_supervision".
        exp_name: Experiment name; used to glob checkpoint files and label the output CSV.
        model_path: Directory containing "{exp_name}_*.pt" checkpoint files.
        model: Model instance used as a template; its state_dict is overwritten for
            each loaded checkpoint and the resulting model is added to the ensemble.
        data_loader: DataLoader yielding `{"image", "mask", "accession_id"}` batches.
        device: Device to run inference on.
        criterion: Loss function called on the ensembled logits and mask.
        dual_scan (bool, optional): If True, splits mask into current/previous-scan
            channels and passes the previous mask to the model. Defaults to False.
        use_tta (bool, optional): If True, averages predictions over 3-axis flip TTA
            in addition to ensembling. Defaults to False.
        spacing: (D, H, W) voxel spacing in mm the volumes were resampled to (nnunet_train.py's/
            nnunet_infer.py's `spacing`), used for the HD95 distance metrics. Must match the actual
            resample spacing — the (3.0, 0.5, 0.5) default is only correct if that happens to be it.

    Returns:
        Dict[str, Any]: Aggregate test metrics, including per-metric means, stds, and
        95% CI half-widths/bounds for loss/dice/WP/PZ/TZ (Dice and HD95), plus a
        nested "region_metrics" dict with mean/std/ci95/ci95_lower/ci95_upper for
        each apex/mid/base x wp/pz/tz x dice/hdf combination.

    Raises:
        FileNotFoundError: If no checkpoint matches "{exp_name}_*.pt" under model_path.
    """
    img_names, loss_list = [], []
    _region_accum: dict = {k: [] for k in _REGION_METRIC_KEYS}

    print(f"Running inference using device: {device}")

    models = []
    for file in glob.glob(f"{model_path}/{exp_name}_*.pt"):
        print(f"Loading {file} file")
        state_dict = torch.load(file, map_location=torch.device(device))
        model.load_state_dict(state_dict)
        if "nnunet" in exp_name:
            model = set_deep_supervision_enabled(False, False, model)
        model.to(device)
        model.eval()
        models.append(model)

    if not models:
        raise FileNotFoundError(
            f"No checkpoints matching '{exp_name}_*.pt' found under {model_path}"
        )

    test_dice_metric = DiceMetric(include_background=True, reduction="mean_channel")
    test_dice_metric_batch = DiceMetric(include_background=True, reduction="none")

    test_hdf_metric_batch = HausdorffDistanceMetric(
        include_background=True,
        distance_metric="euclidean",
        percentile=95,
        reduction="none",
    )

    test_bar = tqdm(data_loader, total=len(data_loader))

    for batch in test_bar:
        names = batch["accession_id"]
        image, mask = batch["image"], batch["mask"]

        if dual_scan:
            mask, mask_prev = mask[:, :3, ...], mask[:, 3:, ...]

            image, mask, mask_prev = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask_prev, "b c h w d -> b c d h w").float().to(device),
            )
        else:
            image, mask = (
                rearrange(image, "b c h w d -> b c d h w").float().to(device),
                rearrange(mask, "b c h w d -> b c d h w").float().to(device),
            )

        # One row per reconstructed VOLUME, not per patch — see the recreate_image call below.
        case_names, patches_per_image = _group_patches_by_case(list(names))
        coords = batch["coord"]
        img_shapes = _per_case_shape(batch["img_shape"], patches_per_image)
        mask_shapes = _per_case_shape(batch["mask_shape"], patches_per_image)

        img_names.append(case_names)

        with torch.no_grad():
            amp_context = (
                torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16)
                if conf.get("bf16", True) and image.device.type == "cuda"
                else nullcontext()
            )
            with amp_context:
                logits = _forward_with_tta_flag(
                    models,
                    image,
                    mask,
                    dual_scan,
                    conf.get("deep_supervision", True),
                    use_tta=use_tta,
                    mask_prev=mask_prev if dual_scan else None,
                )
                loss = criterion(logits, mask)
        torch.cuda.empty_cache()
        gc.collect()

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        # Reassemble the patch grid into whole volumes BEFORE scoring, which is what makes the
        # reported Dice/HD95 per-subject rather than per-patch
        reconstruction_mask_shapes = (
            [(mask.shape[1], *shape[1:]) for shape in mask_shapes]
            if dual_scan
            else mask_shapes
        )
        reconstructed_logits, _, _, reconstructed_masks = recreate_image(
            len(case_names),
            rearrange(logits, "b c d h w -> b c h w d").detach().cpu(),
            rearrange(logits, "b c d h w -> b c h w d").detach().cpu(),
            rearrange(image, "b c d h w -> b c h w d").detach().cpu(),
            rearrange(mask, "b c d h w -> b c h w d").detach().cpu(),
            img_shapes,
            reconstruction_mask_shapes,
            coords,
            patches_per_image,
        )
        logits = torch.stack(
            [rearrange(v, "c h w d -> c d h w") for v in reconstructed_logits]
        )
        mask = torch.stack(
            [rearrange(v, "c h w d -> c d h w") for v in reconstructed_masks]
        )

        metric_logits = logits.detach().cpu()
        metric_mask = mask.detach().cpu()

        # Convert to bool and move to CPU for HausdorffDistanceMetric
        test_dice_metric(metric_logits, metric_mask)
        test_dice_metric_batch(metric_logits.long(), metric_mask.long())

        logits_hdf = metric_logits.bool()
        mask_hdf = metric_mask.bool()

        test_hdf_metric_batch(logits_hdf, mask_hdf, spacing=spacing)
        loss_list.append(loss.detach().cpu().item())

        # Regional (apex / mid-gland / base) metrics
        region_batch = _gland_region_metrics(
            metric_logits, metric_mask, spacing=spacing
        )
        for k, v_list in region_batch.items():
            _region_accum[k].extend(v_list)

    # One float per subject, per zone; channel order [whole_gland, pz, tz].
    test_metric = test_dice_metric.aggregate().detach().cpu().numpy().flatten().tolist()
    test_metric_wp, test_metric_pz, test_metric_tz = (
        test_dice_metric_batch.aggregate().detach().cpu().T.tolist()
    )
    metric_wp_hdf, metric_pz_hdf, metric_tz_hdf = (
        test_hdf_metric_batch.aggregate().detach().cpu().T.tolist()
    )
    test_dice_metric.reset()
    test_dice_metric_batch.reset()
    test_hdf_metric_batch.reset()

    img_names = [
        item
        for sublist in img_names
        for item in (sublist if isinstance(sublist, list) else [sublist])
    ]

    df = pd.DataFrame(
        {
            "img_names": img_names,
            "dice_scores": test_metric,
            "wp": test_metric_wp,
            "pz": test_metric_pz,
            "tz": test_metric_tz,
            "wp_hdf": metric_wp_hdf,
            "pz_hdf": metric_pz_hdf,
            "tz_hdf": metric_tz_hdf,
            **{k: _region_accum[k] for k in _REGION_METRIC_KEYS},
        }
    )
    df.to_csv(f"{exp_name}_bootstrap.csv")
    test_dice_mean = np.mean(test_metric)
    test_loss_mean = np.mean(loss_list)
    test_wp_mean = np.mean(test_metric_wp)
    test_pz_mean = np.mean(test_metric_pz)
    test_tz_mean = np.mean(test_metric_tz)
    test_wp_hdf_mean = np.nanmean(metric_wp_hdf)
    test_pz_hdf_mean = np.nanmean(metric_pz_hdf)
    test_tz_hdf_mean = np.nanmean(metric_tz_hdf)

    test_std_loss = np.std(loss_list)
    test_std_dice = np.std(test_metric)
    test_std_wp = np.std(test_metric_wp)
    test_std_pz = np.std(test_metric_pz)
    test_std_tz = np.std(test_metric_tz)
    test_std_wp_hdf = np.nanstd(metric_wp_hdf)
    test_std_pz_hdf = np.nanstd(metric_pz_hdf)
    test_std_tz_hdf = np.nanstd(metric_tz_hdf)

    test_95ci_loss = _ci95_from_values(loss_list)
    test_95ci_dice = _ci95_from_values(test_metric)
    test_95ci_wp = _ci95_from_values(test_metric_wp)
    test_95ci_pz = _ci95_from_values(test_metric_pz)
    test_95ci_tz = _ci95_from_values(test_metric_tz)
    test_95ci_wp_hdf = _ci95_from_values(metric_wp_hdf)
    test_95ci_pz_hdf = _ci95_from_values(metric_pz_hdf)
    test_95ci_tz_hdf = _ci95_from_values(metric_tz_hdf)

    test_95ci_lower_loss, test_95ci_upper_loss = _ci95_bounds(
        test_loss_mean, test_95ci_loss
    )
    test_95ci_lower_dice, test_95ci_upper_dice = _ci95_bounds(
        test_dice_mean, test_95ci_dice
    )
    test_95ci_lower_wp, test_95ci_upper_wp = _ci95_bounds(test_wp_mean, test_95ci_wp)
    test_95ci_lower_pz, test_95ci_upper_pz = _ci95_bounds(test_pz_mean, test_95ci_pz)
    test_95ci_lower_tz, test_95ci_upper_tz = _ci95_bounds(test_tz_mean, test_95ci_tz)
    test_95ci_lower_wp_hdf, test_95ci_upper_wp_hdf = _ci95_bounds(
        test_wp_hdf_mean, test_95ci_wp_hdf
    )
    test_95ci_lower_pz_hdf, test_95ci_upper_pz_hdf = _ci95_bounds(
        test_pz_hdf_mean, test_95ci_pz_hdf
    )
    test_95ci_lower_tz_hdf, test_95ci_upper_tz_hdf = _ci95_bounds(
        test_tz_hdf_mean, test_95ci_tz_hdf
    )

    # Regional (apex / mid-gland / base) summary
    _region_metrics_out: dict = {}
    for region in ("apex", "mid", "base"):
        _region_metrics_out[region] = {}
        for zone in ("wp", "pz", "tz"):
            _region_metrics_out[region][zone] = {}
            for metric in ("dice", "hdf"):
                k = f"{region}_{zone}_{metric}"
                vals = np.array(_region_accum[k], dtype=float)
                n_v = int(np.sum(~np.isnan(vals)))
                mean_v = float(np.nanmean(vals)) if n_v else float("nan")
                std_v = float(np.nanstd(vals)) if n_v else float("nan")
                ci_v = _ci95_from_values(vals)
                ci_lower_v, ci_upper_v = _ci95_bounds(mean_v, ci_v)
                _region_metrics_out[region][zone][metric] = {
                    "mean": mean_v,
                    "std": std_v,
                    "ci95": ci_v,
                    "ci95_lower": ci_lower_v,
                    "ci95_upper": ci_upper_v,
                }

    return {
        "test_loss_mean": test_loss_mean,
        "test_dice_mean": test_dice_mean,
        "test_wp_mean": test_wp_mean,
        "test_pz_mean": test_pz_mean,
        "test_tz_mean": test_tz_mean,
        "test_wp_hdf_mean": test_wp_hdf_mean,
        "test_pz_hdf_mean": test_pz_hdf_mean,
        "test_tz_hdf_mean": test_tz_hdf_mean,
        "test_std_loss": test_std_loss,
        "test_std_dice": test_std_dice,
        "test_std_wp": test_std_wp,
        "test_std_pz": test_std_pz,
        "test_std_tz": test_std_tz,
        "test_std_wp_hdf": test_std_wp_hdf,
        "test_std_pz_hdf": test_std_pz_hdf,
        "test_std_tz_hdf": test_std_tz_hdf,
        "test_95ci_loss": test_95ci_loss,
        "test_95ci_dice": test_95ci_dice,
        "test_95ci_wp": test_95ci_wp,
        "test_95ci_pz": test_95ci_pz,
        "test_95ci_tz": test_95ci_tz,
        "test_95ci_wp_hdf": test_95ci_wp_hdf,
        "test_95ci_pz_hdf": test_95ci_pz_hdf,
        "test_95ci_tz_hdf": test_95ci_tz_hdf,
        "test_95ci_lower_loss": test_95ci_lower_loss,
        "test_95ci_upper_loss": test_95ci_upper_loss,
        "test_95ci_lower_dice": test_95ci_lower_dice,
        "test_95ci_upper_dice": test_95ci_upper_dice,
        "test_95ci_lower_wp": test_95ci_lower_wp,
        "test_95ci_upper_wp": test_95ci_upper_wp,
        "test_95ci_lower_pz": test_95ci_lower_pz,
        "test_95ci_upper_pz": test_95ci_upper_pz,
        "test_95ci_lower_tz": test_95ci_lower_tz,
        "test_95ci_upper_tz": test_95ci_upper_tz,
        "test_95ci_lower_wp_hdf": test_95ci_lower_wp_hdf,
        "test_95ci_upper_wp_hdf": test_95ci_upper_wp_hdf,
        "test_95ci_lower_pz_hdf": test_95ci_lower_pz_hdf,
        "test_95ci_upper_pz_hdf": test_95ci_upper_pz_hdf,
        "test_95ci_lower_tz_hdf": test_95ci_lower_tz_hdf,
        "test_95ci_upper_tz_hdf": test_95ci_upper_tz_hdf,
        "region_metrics": _region_metrics_out,
    }


def generate_predictions(
    predictions_dir: str,
    conf: dict[str, Any],
    exp_name: str,
    model_path: str,
    model: nn.Module,
    test_datalist: list[dict[str, Any]],
    preprocessing: Compose,
    patch_iter: Any,
    device: torch.device,
) -> None:
    """Run inference over test_datalist and save each case's prediction as a NIfTI in native space.

    Loads one checkpoint per file matching "{model_path}/{exp_name}_*.pt" into an ensemble (same
    convention as inference_func). For each case, applies `preprocessing` — a MetaTensor-preserving
    Compose over just the "image" key (LoadImaged/Orientationd/Spacingd/SpatialPadd/
    CenterSpatialCropd/NormalizeIntensityd, built by the caller to mirror dataset.py's build_loader +
    preprocess.build_case_transform, minus the mask keys and the final `.as_tensor()` those strip).

    Prediction is PER PATCH, over `patch_iter`'s deterministic grid, and the patch predictions are
    then stitched back into a full volume — what MambaX-Net's generate_predictions did (it consumed
    a PatchIterd-backed DataLoader and reassembled with recreate_image). It matters beyond fidelity:
    the model is fully convolutional but its decoder only lines up when each spatial extent divides
    by that axis' total downsampling factor, which a whole volume (depth 21-25 against a factor of
    4) does not satisfy — forwarding one fails outright.

    Only the final native-space mapping departs from upstream: it uses monai.transforms.Invertd to
    replay `preprocessing`'s recorded history in reverse — the mechanism
    fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation's inference bundle uses
    (Activationsd -> Invertd -> AsDiscreted) — rather than MambaX-Net's
    resample_mask_to_original_space/set_resampling_metadata NIfTI-header-extension trail, because
    this tutorial's data pipeline never writes those extensions.

    Saves one 3-channel (whole_gland, pz, tz) NIfTI per case — channel order matching
    dataset.py's combine_masks — named "{accession_id}.nii.gz", under predictions_dir, in the case's
    own native affine/spacing/orientation. Ground-truth masks and the source image are not re-saved;
    they already live in XNAT.

    Args:
        predictions_dir: Output directory for prediction NIfTI files.
        conf: Config dict; only "deep_supervision" is read here.
        exp_name: Experiment name; used to glob checkpoint files.
        model_path: Directory containing "{exp_name}_*.pt" checkpoint files.
        model: Model instance used as a template; its state_dict is overwritten per checkpoint.
        test_datalist: `{IMAGE_KEY, ..., "accession_id"}` dicts, e.g. from
            FLIP_BASE.get_case_list — only IMAGE_KEY and "accession_id" are used.
        preprocessing: The traced, image-only preprocessing Compose (see nnunet_infer.py). Must be
            the exact chain applied before the model saw the image, so Invertd can undo it.
        patch_iter: A `monai.data.PatchIterd` over the "image" key alone, with the same patch size
            training used. Its per-patch coordinates drive the stitch.
        device: Device to run inference on.

    Raises:
        FileNotFoundError: If no checkpoint matches "{exp_name}_*.pt" under model_path.
    """
    os.makedirs(predictions_dir, exist_ok=True)

    models = []
    for file in glob.glob(f"{model_path}/{exp_name}_*.pt"):
        print(f"Loading {file} file")
        state_dict = torch.load(file, map_location=torch.device(device))
        model.load_state_dict(state_dict)
        if "nnunet" in exp_name:
            model = set_deep_supervision_enabled(False, False, model)
        model.to(device)
        model.eval()
        models.append(model)

    if not models:
        raise FileNotFoundError(
            f"No checkpoints matching '{exp_name}_*.pt' found under {model_path}"
        )

    activate = Activationsd(keys="pred", sigmoid=True)
    invert = Invertd(
        keys="pred",
        transform=preprocessing,
        orig_keys="image",
        nearest_interp=False,
        to_tensor=True,
    )

    failed: list[str] = []
    for item in tqdm(test_datalist):
        accession_id = item["accession_id"]
        try:
            data = preprocessing({"image": item[IMAGE_KEY]})
            volume = data["image"]  # (C, H, W, D) in preprocessed space

            patches, coords = [], []
            for patch, coord in patch_iter({"image": volume}):
                patch_image = patch["image"]
                patches.append(
                    patch_image.as_tensor()
                    if hasattr(patch_image, "as_tensor")
                    else patch_image
                )
                coords.append(coord)

            # The network takes (b, c, d, h, w); _forward_with_tta_flag passes its argument
            # straight to model(), so the axis order is the caller's job — train_seg rearranges
            # here too.
            batch = (
                rearrange(torch.stack(patches), "b c h w d -> b c d h w")
                .float()
                .to(device)
            )

            with torch.no_grad():
                logits = _forward_with_tta_flag(
                    models,
                    batch,
                    None,
                    False,
                    conf.get("deep_supervision", True),
                    use_tta=False,
                    mask_prev=None,
                )

            logits = rearrange(logits, "b c d h w -> b c h w d").detach().cpu()

            stitched = torch.zeros(
                (logits.shape[1], *volume.shape[1:]), dtype=logits.dtype
            )
            for logit, coord in zip(logits, coords):
                _write_patch(stitched, logit, coord)

            data["pred"] = MetaTensor(stitched, meta=data["image"].meta)
            data = activate(data)
            data = invert(data)

            pred = (
                (data["pred"] > 0.5).to(torch.uint8).numpy()
            )  # (C, H, W, D), C=[whole_gland, pz, tz]
            affine = np.asarray(data["pred"].affine)

            nib.Nifti1Image(np.moveaxis(pred, 0, -1), affine).to_filename(
                os.path.join(predictions_dir, f"{accession_id}.nii.gz")
            )
        except Exception as err:
            print(f"⚠️ Inference failed for accession_id={accession_id}: {err}")
            failed.append(accession_id)

    if failed:
        print(f"WARNING: {len(failed)} case(s) failed during inference: {failed}")
    else:
        print("\nAll files processed successfully!")
