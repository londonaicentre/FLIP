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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/train_helpers.py,
# flattened into this tutorial's directory: the mambax_net.* package imports are
# rewritten as flat module imports. Everything below the import block is verbatim.

import gc
import glob
import inspect
import os
import random
from contextlib import nullcontext
from logging import DEBUG, FileHandler, Formatter, Logger, StreamHandler, getLogger
from typing import Any, Dict, List, Optional, Tuple, Union

import nibabel as nib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import wandb
from dotenv import load_dotenv
from einops import rearrange
from monai.metrics import DiceMetric, HausdorffDistanceMetric
from nnunetv2.training.lr_scheduler.polylr import PolyLRScheduler
from optuna.integration.wandb import WeightsAndBiasesCallback
from scipy import stats
from torch.cuda.amp import GradScaler
from torch.optim.lr_scheduler import (
    CosineAnnealingLR,
    CosineAnnealingWarmRestarts,
    OneCycleLR,
    ReduceLROnPlateau,
    StepLR,
)
from torch.utils.tensorboard import SummaryWriter
from tqdm.auto import tqdm

from metrics_utils import AverageMeter
from nnunet_arch import set_deep_supervision_enabled
from warmup import GradualWarmupSchedulerV2
from nifti_utilities import convert_orientation
from resample import (
    get_resampling_metadata,
    resample_mask_to_original_space,
    unpad_depth,
)


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


def detect_required_flips(original_affine, current_affine):
    """
    Detect which spatial axes need to be flipped to match orientations.

    Args:
        original_affine: Original image affine matrix
        current_affine: Current space affine matrix

    Returns:
        Tuple of (flip_x, flip_y, flip_z) boolean flags
    """
    # Get diagonal elements (voxel sizes with orientation)
    orig_diag = np.array([original_affine[i, i] for i in range(3)])
    curr_diag = np.array([current_affine[i, i] for i in range(3)])

    # Check signs - if they differ, we need to flip that axis
    orig_signs = orig_diag < 0
    curr_signs = curr_diag < 0

    flip_x = orig_signs[0] != curr_signs[0]
    flip_y = orig_signs[1] != curr_signs[1]
    flip_z = orig_signs[2] != curr_signs[2]

    return flip_x, flip_y, flip_z


def get_scheduler(
    conf: Dict[str, Any],
    optimizer: torch.optim.Optimizer,
    scheduler_name: str,
    train_loader: torch.utils.data.DataLoader,
    step_size: int,
) -> Union[
    torch.optim.lr_scheduler._LRScheduler, torch.optim.lr_scheduler.ReduceLROnPlateau
]:
    """Get the learning rate scheduler.

    Args:
        conf (Dict[str, Any]): Configuration dictionary.
        optimizer (torch.optim.Optimizer): The optimizer to schedule.
        scheduler_name (str): The name of the scheduler.
        train_loader (torch.utils.data.DataLoader): The training data loader.
        step_size (int): Step size for the scheduler.

    Raises:
        ValueError: If the scheduler is not found.

    Returns:
        Union[torch.optim.lr_scheduler._LRScheduler, torch.optim.lr_scheduler.ReduceLROnPlateau]: The learning rate scheduler.
    """
    if scheduler_name == "ReduceLROnPlateau":
        scheduler = ReduceLROnPlateau(
            optimizer,
            mode="max",
            factor=conf["factor"],
            patience=conf["patience"],
            verbose=True,
            eps=conf["eps"],
        )
    elif scheduler_name == "CosineAnnealingLR":
        scheduler = CosineAnnealingLR(
            optimizer, T_max=conf["T_max"], eta_min=conf["min_lr"], last_epoch=-1
        )
    elif scheduler_name == "CosineAnnealingWarmRestarts":
        scheduler = CosineAnnealingWarmRestarts(
            optimizer, T_0=conf["T_0"], T_mult=1, eta_min=conf["min_lr"], last_epoch=-1
        )
    elif scheduler_name == "GradualWarmupSchedulerV2":
        scheduler_cosine = CosineAnnealingLR(
            optimizer, T_max=conf["T_max"], eta_min=conf["min_lr"], last_epoch=-1
        )
        scheduler = GradualWarmupSchedulerV2(
            optimizer,
            multiplier=conf["warmup_factor"],
            total_epoch=conf["warmup_epoch"],
            after_scheduler=scheduler_cosine,
        )
    elif scheduler_name == "StepLR":
        scheduler = StepLR(optimizer, step_size=step_size, gamma=0.7, last_epoch=-1)
    elif scheduler_name == "OneCycle":
        scheduler = OneCycleLR(
            optimizer,
            max_lr=conf["lr"],
            pct_start=0.45,
            cycle_momentum=True,
            steps_per_epoch=len(train_loader),
            epochs=conf["epochs"],
        )
    elif scheduler_name == "PolyLR":
        scheduler = PolyLRScheduler(optimizer, conf["lr"], conf["epochs"])
    else:
        raise ValueError(f"Scheduler {scheduler_name} not found")

    return scheduler



def train_seg(
    epoch: int,
    conf: Dict[str, Any],
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
    train_loader: torch.utils.data.DataLoader,
    val_loader: torch.utils.data.DataLoader,
    tb_writer: SummaryWriter,
    scaler: GradScaler,
    criterion: nn.Module,
    device: torch.device,
    dual_scan: bool = False,
):
    """Run one training epoch followed by validation for a segmentation model.

    Iterates over train_loader, computes the (optionally deep-supervised) loss via
    criterion, backpropagates with optional AMP/GradScaler, steps the OneCycle
    scheduler per batch if configured, tracks running Dice metrics (overall/WP/PZ/TZ),
    and logs per-batch metrics to WandB and TensorBoard. Then switches the model to
    eval mode and repeats the same forward/metric computation (without backprop) over
    val_loader. Supports dual-scan inputs (mask split into current and
    previous-scan channels) and channels_last_3d memory format when the model's
    parameters are already in that format.

    Args:
        epoch (int): Current epoch number, used for logging.
        conf (Dict[str, Any]): Training configuration (bf16, deep_supervision, max_norm,
            norm_type, scheduler, etc.).
        model (nn.Module): Segmentation model being trained.
        optimizer (torch.optim.Optimizer): Optimizer for the model parameters.
        scheduler (torch.optim.lr_scheduler._LRScheduler): LR scheduler; stepped per
            batch only when conf["scheduler"] == "OneCycle".
        train_loader (torch.utils.data.DataLoader): Training loader yielding
            (image, mask, _, _) batches.
        val_loader (torch.utils.data.DataLoader): Validation loader with the same batch format.
        tb_writer (SummaryWriter): TensorBoard writer, or None to skip TensorBoard logging.
        scaler (GradScaler): Gradient scaler used when conf["bf16"] is True.
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
        logits_list: Union[List[torch.Tensor], Tuple[torch.Tensor, ...]],
    ) -> List[torch.Tensor]:
        """Build one target per deep supervision output.

        DeepSupervisionWrapper expects a list of targets with the same length as
        the list of model outputs. We resize the full-resolution target to each
        output's spatial size using nearest-neighbor interpolation.
        """

        targets: List[torch.Tensor] = []
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

    train_bar = tqdm(enumerate(train_loader), total=len(train_loader))
    for train_batch_idx, (image, mask, _, _) in train_bar:
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

        if conf["bf16"]:
            with torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16):
                if dual_scan:
                    logits = model(image, mask_prev)
                else:
                    logits = model(image)

                if conf["deep_supervision"]:
                    pred_list = (
                        logits if isinstance(logits, (list, tuple)) else [logits]
                    )
                    loss = criterion(pred_list, _build_ds_targets(mask, pred_list))
                    logits = pred_list[0]
                else:
                    loss = criterion(logits, mask)

            logits = torch.sigmoid(logits)
            logits = (logits > 0.5).float()

            train_dice_metric(logits.detach(), mask.detach())
            train_dice_metric_batch(logits.detach(), mask.detach())

            scaler.scale(loss).backward()

            if conf["max_norm"] is not None:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), conf["max_norm"], conf["norm_type"]
                )
            scaler.step(optimizer)
            scaler.update()
        else:
            if dual_scan:
                logits = model(image, mask_prev)
            else:
                logits = model(image)

            if conf["deep_supervision"]:
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
            optimizer.step()

        if conf["scheduler"] == "OneCycle":
            scheduler.step()

        train_loss.update(loss.detach())

        train_metric, train_metric_wp, train_metric_pz, train_metric_tz = (
            calculate_metrics(train_dice_metric, train_dice_metric_batch, conf)
        )
        train_mean_dice.update(train_metric)
        train_wp_dice.update(train_metric_wp)
        train_pz_dice.update(train_metric_pz)
        train_tz_dice.update(train_metric_tz)

        train_bar.set_description("average train loss: %.5f" % (train_loss.avg))

        train_log = {
            "train/train_epoch": epoch,
            "train/loss": loss.detach(),
            "train/mean_dice": train_metric,
            "train/wp_dice": train_metric_wp,
            "train/pz_dice": train_metric_pz,
            "train/tz_dice": train_metric_tz,
        }

        wandb.log(train_log)

        if tb_writer is not None:
            tb_writer.add_scalar("train/loss", loss.detach(), train_batch_idx)
            tb_writer.add_scalar("train/mean_dice", train_metric, train_batch_idx)
            tb_writer.add_scalar("train/wp_dice", train_metric_wp, train_batch_idx)
            tb_writer.add_scalar("train/pz_dice", train_metric_pz, train_batch_idx)
            tb_writer.add_scalar("train/tz_dice", train_metric_tz, train_batch_idx)
            tb_writer.add_scalar("LR", optimizer.param_groups[0]["lr"], train_batch_idx)

    wandb.log({"lr": optimizer.param_groups[0]["lr"]})

    model.eval()

    val_loss = AverageMeter()
    val_mean_dice = AverageMeter()
    val_wp_dice = AverageMeter()
    val_pz_dice = AverageMeter()
    val_tz_dice = AverageMeter()

    val_dice_metric = DiceMetric(include_background=True, reduction="mean")
    val_dice_metric_batch = DiceMetric(include_background=True, reduction="mean_batch")

    val_bar = tqdm(enumerate(val_loader), total=len(val_loader))
    for val_batch_idx, (image, mask, _, _) in val_bar:
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

            if conf["deep_supervision"]:
                pred_list = logits if isinstance(logits, (list, tuple)) else [logits]
                loss = criterion(pred_list, _build_ds_targets(mask, pred_list))
                logits = pred_list[0]
            else:
                loss = criterion(logits, mask)

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        val_dice_metric(logits.detach(), mask.detach())
        val_dice_metric_batch(logits.detach(), mask.detach())

        val_metric, val_metric_wp, val_metric_pz, val_metric_tz = calculate_metrics(
            val_dice_metric, val_dice_metric_batch, conf
        )

        val_loss.update(loss.detach())
        val_mean_dice.update(val_metric)
        val_wp_dice.update(val_metric_wp)
        val_pz_dice.update(val_metric_pz)
        val_tz_dice.update(val_metric_tz)

        val_bar.set_description("average val loss: %.5f" % (val_loss.avg))

    val_log = {
        "val/epoch": epoch,
        "val/loss": val_loss.avg,
        "val/mean_dice": val_mean_dice.avg,
        "val/wp_dice": val_wp_dice.avg,
        "val/pz_dice": val_pz_dice.avg,
        "val/tz_dice": val_tz_dice.avg,
    }

    wandb.log(val_log)

    # Log data for TensorBoard
    if tb_writer is not None:
        tb_writer.add_scalar("val/loss", val_loss.avg, epoch)
        tb_writer.add_scalar("val/mean_dice", val_mean_dice.avg, epoch)
        tb_writer.add_scalar("val/wp_dice", val_wp_dice.avg, epoch)
        tb_writer.add_scalar("val/pz_dice", val_pz_dice.avg, epoch)
        tb_writer.add_scalar("val/tz_dice", val_tz_dice.avg, epoch)

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
_TTA_FLIP_DIMS: List[List[int]] = [[2], [3], [4]]


def _plain_ensemble_forward(
    models: list,
    image: torch.Tensor,
    mask: torch.Tensor,
    dual_scan: bool,
    deep_supervision: bool,
    mask_prev: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    """Average raw logits across folds without any TTA."""
    all_logits: List[torch.Tensor] = []
    for model in models:
        if dual_scan:
            logits = model(image, mask_prev)
        else:
            sig = inspect.signature(model.forward)
            if "mask" in sig.parameters:
                img_d = (
                    torch.cat([image, image], dim=1) if image.shape[1] == 1 else image
                )
                logits = model(img_d, mask)
            else:
                logits = model(image)
        if deep_supervision:
            logits = logits[0]
        all_logits.append(logits)
    return torch.mean(torch.stack(all_logits), dim=0)


def _tta_ensemble_forward(
    models: list,
    image: torch.Tensor,
    mask: torch.Tensor,
    dual_scan: bool,
    deep_supervision: bool,
    mask_prev: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    """Average raw logits across folds and 3-axis TTA flips.

    Runs 4 forward passes per fold (original + flip-D + flip-H + flip-W),
    un-flips each output, then averages all predictions.
    """
    all_logits: List[torch.Tensor] = []
    tta_views = [None] + _TTA_FLIP_DIMS

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


def _forward_with_tta_flag(
    models: list,
    image: torch.Tensor,
    mask: torch.Tensor,
    dual_scan: bool,
    deep_supervision: bool,
    use_tta: bool = False,
    mask_prev: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    """Dispatch to TTA or plain ensemble based on the use_tta flag."""
    if use_tta:
        return _tta_ensemble_forward(
            models, image, mask, dual_scan, deep_supervision, mask_prev
        )
    return _plain_ensemble_forward(
        models, image, mask, dual_scan, deep_supervision, mask_prev
    )


# Keys for gland-region metrics (used by inference_func + callers) ──
_REGION_METRIC_KEYS: List[str] = [
    f"{r}_{z}_{m}"
    for r in ("apex", "mid", "base")
    for z in ("wp", "pz", "tz")
    for m in ("dice", "hdf")
]


def inference_func(
    conf,
    exp_name,
    model_path,
    model,
    data_loader,
    device,
    num_samples,
    criterion,
    dataset_name="xnat",
    dual_scan=False,
    use_tta=False,
):
    """Run inference/evaluation over data_loader with a fold ensemble and report test metrics.

    Loads one model checkpoint per file matching "{model_path}/{exp_name}_*.pt" into
    an ensemble, then for each batch: forwards through the ensemble (optionally with
    3-axis TTA flip averaging via _forward_with_tta_flag), computes the loss,
    thresholds logits into binary predictions, reconstructs full-size volumes from
    patches when needed (recreate_image), optionally applies postprocessing, and
    accumulates Dice, HD95, and per-region (apex/mid/base) WP/PZ/TZ metrics. Masks
    are flipped along axis 4 for the ProstateX dataset before use. Saves a
    per-subject bootstrap score table to "{exp_name}_bootstrap.csv" and logs it as a
    WandB artifact. Finally computes means, stds, and 95% CI bounds (overall and per
    gland region) for all metrics.

    Args:
        conf: Config dict/object with keys "bf16", "deep_supervision", "postprocess",
            "min_size", "distance_threshold".
        exp_name: Experiment name; used to glob checkpoint files and label the output CSV.
        model_path: Directory containing "{exp_name}_*.pt" checkpoint files.
        model: Model instance used as a template; its state_dict is overwritten for
            each loaded checkpoint and the resulting model is added to the ensemble.
        data_loader: DataLoader yielding batches unpacked by _unpack_inference_batch.
        device: Device to run inference on.
        num_samples: Accepted for interface consistency with callers; unused in the body.
        criterion: Loss function called on the ensembled logits and mask.
        dataset_name (str, optional): Dataset identifier; masks are flipped along
            axis 4 when set to "ProstateX". Defaults to "xnat".
        dual_scan (bool, optional): If True, splits mask into current/previous-scan
            channels and passes the previous mask to the model. Defaults to False.
        use_tta (bool, optional): If True, averages predictions over 3-axis flip TTA
            in addition to ensembling. Defaults to False.

    Returns:
        Dict[str, Any]: Aggregate test metrics, including per-metric means, stds, and
        95% CI half-widths/bounds for loss/dice/WP/PZ/TZ (Dice and HD95), plus a
        nested "region_metrics" dict with mean/std/ci95/ci95_lower/ci95_upper for
        each apex/mid/base x wp/pz/tz x dice/hdf combination.
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
        if any(name in exp_name for name in ("segmamba", "swinunetr")):
            model = model.to(memory_format=torch.channels_last_3d)
        model.eval()
        models.append(model)

    use_cl3d = any(
        param.is_contiguous(memory_format=torch.channels_last_3d)
        for mdl in models
        for param in mdl.parameters()
        if param.ndim == 5
    )

    test_dice_metric = DiceMetric(include_background=True, reduction="mean_channel")
    test_dice_metric_batch = DiceMetric(include_background=True, reduction="none")

    test_hdf_metric_batch = HausdorffDistanceMetric(
        include_background=True,
        distance_metric="euclidean",
        percentile=95,
        reduction="none",
    )

    test_bar = tqdm(enumerate(data_loader), total=len(data_loader))

    for test_batch_idx, batch_data in test_bar:
        if batch_data is None:
            continue

        (
            names,
            image,
            mask,
            coords,
            _,
            img_shape,
            mask_shape,
            img_nii,
            mask_nii,
            original_shapes,
            original_spacings,
            original_affines,
            patches_per_image,
        ) = _unpack_inference_batch(batch_data)

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

        if use_cl3d:
            image = image.contiguous(memory_format=torch.channels_last_3d)
            mask = mask.contiguous(memory_format=torch.channels_last_3d)
            if dual_scan:
                mask_prev = mask_prev.contiguous(memory_format=torch.channels_last_3d)

        # ProstateX masks need to be flipped.
        if dataset_name == "ProstateX":
            mask = torch.flip(mask, [4])

        img_names.append(names)

        with torch.no_grad():
            amp_context = (
                torch.amp.autocast(device_type="cuda", dtype=torch.bfloat16)
                if conf.get("bf16", False) and image.device.type == "cuda"
                else nullcontext()
            )
            with amp_context:
                logits = _forward_with_tta_flag(
                    models,
                    image,
                    mask,
                    dual_scan,
                    conf["deep_supervision"],
                    use_tta=use_tta,
                    mask_prev=mask_prev if dual_scan else None,
                )
                loss = criterion(logits, mask)
        torch.cuda.empty_cache()
        gc.collect()

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        needs_reconstruction = (
            patches_per_image is not None or len(names) != logits.shape[0]
        )

        if needs_reconstruction:
            reconstruction_mask_shapes = (
                [(mask.shape[1], *tuple(shape[1:])) for shape in mask_shape]
                if dual_scan
                else mask_shape
            )
            reconstructed_logits, _, _, reconstructed_masks = recreate_image(
                len(names),
                rearrange(logits, "b c d h w -> b c h w d").detach().cpu(),
                rearrange(logits, "b c d h w -> b c h w d").detach().cpu(),
                rearrange(image, "b c d h w -> b c h w d").detach().cpu(),
                rearrange(mask, "b c d h w -> b c h w d").detach().cpu(),
                img_shape,
                reconstruction_mask_shapes,
                coords,
                patches_per_image,
            )
            logits = torch.stack(
                [
                    rearrange(logit, "c h w d -> c d h w")
                    for logit in reconstructed_logits
                ]
            )
            mask = torch.stack(
                [
                    rearrange(mask_item, "c h w d -> c d h w")
                    for mask_item in reconstructed_masks
                ]
            )

        if conf["postprocess"]:
            assert (
                conf["min_size"] is not None and conf["distance_threshold"] is not None
            ), "Postprocessing is enabled but min_size and distance_threshold are not set in the config."
            for i, logit in enumerate(logits):
                logit = rearrange(logit, "c d h w -> h w d c")
                logit = postprocess_segmentation(
                    logit,
                    min_size=conf["min_size"],
                    distance_threshold=conf["distance_threshold"],
                )
                logits[i] = rearrange(logit, "h w d c -> c d h w")

        metric_logits = logits.detach().cpu()
        metric_mask = mask.detach().cpu()

        # Convert to bool and move to CPU for HausdorffDistanceMetric
        test_dice_metric(metric_logits, metric_mask)
        test_dice_metric_batch(metric_logits.long(), metric_mask.long())

        logits_hdf = metric_logits.bool()
        mask_hdf = metric_mask.bool()

        # spacing for shape (b, c, d, h, w)
        test_hdf_metric_batch(logits_hdf, mask_hdf, spacing=(3.0, 0.5, 0.5))
        loss_list.append(loss.detach().cpu().item())

        # Regional (apex / mid-gland / base) metrics
        region_batch = _gland_region_metrics(metric_logits.numpy(), metric_mask.numpy())
        for k, v_list in region_batch.items():
            _region_accum[k].extend(v_list)

    (
        test_metric,
        test_metric_wp,
        test_metric_pz,
        test_metric_tz,
        metric_wp_hdf,
        metric_pz_hdf,
        metric_tz_hdf,
    ) = calculate_test_metrics(
        test_dice_metric, test_dice_metric_batch, test_hdf_metric_batch, conf
    )

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
    df_table = wandb.Table(dataframe=df)

    df_table_artifact = wandb.Artifact("bootstrap_scores", type="dataset")
    df_table_artifact.add(df_table, f"{exp_name}_bootstrap_table")
    df_table_artifact.add_file(f"{exp_name}_bootstrap.csv")
    wandb.log_artifact(df_table_artifact)

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


def _unpack_inference_batch(
    batch_data: Tuple[Any, ...],
) -> Tuple[
    Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Optional[List[int]]
]:
    """Unpack an inference batch tuple, filling in patches_per_image for older loaders.

    Args:
        batch_data (Tuple[Any, ...]): Batch tuple, either the current 13-element form
            (names, image, mask, coords, _, img_shape, mask_shape, img_nii, mask_nii,
            original_shapes, original_spacings, original_affines/orientations,
            patches_per_image) or the legacy 12-element form without patches_per_image.

    Returns:
        Tuple[Any, ...]: The 13-element batch tuple, with patches_per_image set to
        None when the input was the legacy 12-element form.

    Raises:
        ValueError: If batch_data has a length other than 12 or 13.
    """
    if len(batch_data) == 13:
        return batch_data

    if len(batch_data) == 12:
        return (*batch_data, None)

    raise ValueError(
        f"Unexpected inference batch format with {len(batch_data)} elements"
    )


def generate_predictions(
    prob_maps_dir,
    pred_dir,
    conf,
    exp_name,
    model_path,
    model,
    data_loader,
    device,
    dataset_name="xnat",
    dual_scan=False,
    log_preds_to_wandb=True,
    skip_existing=False,
    use_tta=False,
    weight_glob=None,
):
    """Run inference and save prediction/probability NIfTI files to disk for each sample.

    Loads one model checkpoint per file matching weight_glob (default
    "{exp_name}_*.pt") in model_path into an ensemble, then for each batch in
    data_loader: forwards through the ensemble (optionally with TTA via
    _forward_with_tta_flag), reconstructs full-size volumes from patches when needed
    (recreate_image), optionally postprocesses the prediction, and writes per-subject
    outputs to disk: cropped-space WP/PZ/TZ probability maps and hard predictions,
    ground-truth masks, the input image, and versions of the predictions/masks
    resampled back to each subject's original space and orientation (with depth
    padding removed if it was applied during preprocessing). Skips batches that fail
    to unpack (e.g. abnormal image orientation) or whose files already exist on disk
    when skip_existing is True, and tracks batches that raise RuntimeError during
    model inference or image reconstruction as failed. ProstateX masks are flipped
    along axis 4 before use. Prints and writes text logs of skipped/abnormal/failed
    files under pred_dir and, if requested, logs the saved WP/PZ+TZ prediction
    directories as WandB artifacts.

    Args:
        prob_maps_dir: Root directory for saved probability maps (pz/, tz/, and their
            resampled/ subfolders are created under it).
        pred_dir: Root directory for saved predictions, masks, and images (preds/,
            masks/, images/, and resampled/ subfolders are created under it).
        conf: Config dict/object with keys "bf16", "deep_supervision", "postprocess",
            "min_size", "distance_threshold".
        exp_name: Experiment name; used to glob checkpoint files (when weight_glob is
            None) and to decide whether deep supervision is disabled for nnU-Net models.
        model_path: Directory containing checkpoint files matching weight_glob.
        model: Model instance used as a template; its state_dict is overwritten for
            each loaded checkpoint and the resulting model is added to the ensemble.
        data_loader: DataLoader yielding batches unpacked by _unpack_inference_batch.
        device: Device to run inference on.
        dataset_name (str, optional): Dataset identifier; masks are flipped along
            axis 4 when set to "ProstateX". Defaults to "xnat".
        dual_scan (bool, optional): If True, splits mask into current/previous-scan
            channels and passes the previous mask to the model. Defaults to False.
        log_preds_to_wandb (bool, optional): If True, logs the saved WP and PZ/TZ
            prediction directories as WandB artifacts. Defaults to True.
        skip_existing (bool, optional): If True, skips a batch when its resampled WP
            and PZ/TZ prediction files already exist on disk. Defaults to False.
        use_tta (bool, optional): If True, averages predictions over 3-axis flip TTA
            in addition to ensembling. Defaults to False.
        weight_glob (str, optional): Glob pattern (relative to model_path) for
            checkpoint files; defaults to "{exp_name}_*.pt" when None.

    Returns:
        None. Prediction, probability, mask, and image NIfTI files are written under
        prob_maps_dir and pred_dir as a side effect, and skipped/abnormal/failed file
        lists are printed and optionally written to text files in pred_dir.
    """
    print(f"Running inference using {device} to save nifti files")

    os.makedirs(prob_maps_dir, exist_ok=True)
    os.makedirs(f"{prob_maps_dir}/tz", exist_ok=True)
    os.makedirs(f"{prob_maps_dir}/pz", exist_ok=True)

    os.makedirs(pred_dir, exist_ok=True)
    os.makedirs(f"{pred_dir}/resampled/masks/wp/", exist_ok=True)
    os.makedirs(f"{pred_dir}/resampled/masks/pz_tz/", exist_ok=True)
    os.makedirs(f"{pred_dir}/resampled/preds/wp/", exist_ok=True)
    os.makedirs(f"{pred_dir}/resampled/preds/pz_tz/", exist_ok=True)

    # Track failed and skipped files for logging
    failed_files = []
    skipped_files = []
    abnormal_files = []  # Track files with abnormal orientations
    os.makedirs(f"{prob_maps_dir}/resampled/tz/", exist_ok=True)
    os.makedirs(f"{prob_maps_dir}/resampled/pz/", exist_ok=True)

    os.makedirs(f"{pred_dir}/preds", exist_ok=True)
    os.makedirs(f"{pred_dir}/preds/wp", exist_ok=True)
    os.makedirs(f"{pred_dir}/preds/pz_tz", exist_ok=True)
    os.makedirs(f"{pred_dir}/images", exist_ok=True)
    os.makedirs(f"{pred_dir}/masks", exist_ok=True)

    pz_artifact = wandb.Artifact(name="pz_tz_predictions", type="dataset")
    wp_artifact = wandb.Artifact(name="wp_predictions", type="dataset")

    models = []
    weight_pattern = weight_glob or f"{exp_name}_*.pt"
    for file in glob.glob(os.path.join(model_path, weight_pattern)):
        print(f"Loading {file} file")
        state_dict = torch.load(file, map_location=torch.device(device))
        model.load_state_dict(state_dict)
        if "nnunet" in exp_name:
            model = set_deep_supervision_enabled(False, False, model)
        model.to(device)
        if any(name in exp_name for name in ("segmamba", "swinunetr")):
            model = model.to(memory_format=torch.channels_last_3d)
        model.eval()
        models.append(model)

    use_cl3d = any(
        param.is_contiguous(memory_format=torch.channels_last_3d)
        for mdl in models
        for param in mdl.parameters()
        if param.ndim == 5
    )

    test_bar = tqdm(enumerate(data_loader), total=len(data_loader))
    for test_batch_idx, batch_data in test_bar:
        # Skip None batches (all items filtered out due to errors)
        if batch_data is None:
            continue

        try:
            (
                names,
                image,
                mask,
                coords,
                _,
                im_shape,
                mask_shape,
                img_nii,
                mask_nii,
                original_shapes,
                original_spacings,
                original_orientations,
                patches_per_image,
            ) = _unpack_inference_batch(batch_data)
        except (ValueError, RuntimeError) as e:
            # Handle data loading errors (e.g., abnormal image orientations)
            error_msg = str(e)
            if "Abnormal image orientation" in error_msg:
                print(f"Skipping batch {test_batch_idx} due to abnormal orientation")
                abnormal_files.append(f"batch_{test_batch_idx}")
            else:
                print(f"Error loading batch {test_batch_idx}: {e}")
            continue

        # Skip empty batches
        if len(names) == 0:
            continue
        # Check if we should skip existing files
        if skip_existing:
            all_exist = True
            for name in names:
                pz_tz_path = f"{pred_dir}/resampled/preds/pz_tz/{name}.nii.gz"
                wp_path = f"{pred_dir}/resampled/preds/wp/{name}.nii.gz"
                if not (os.path.exists(pz_tz_path) and os.path.exists(wp_path)):
                    all_exist = False
                    break
            if all_exist:
                skipped_files.extend(names)
                continue

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

        if use_cl3d:
            image = image.contiguous(memory_format=torch.channels_last_3d)
            mask = mask.contiguous(memory_format=torch.channels_last_3d)
            if dual_scan:
                mask_prev = mask_prev.contiguous(memory_format=torch.channels_last_3d)

        # ProstateX masks need to be flipped.
        if dataset_name == "ProstateX":
            mask = torch.flip(mask, [4])

        try:
            with torch.no_grad():
                amp_context = (
                    torch.amp.autocast(device_type="cuda", dtype=torch.float16)
                    if conf.get("bf16", False) and image.device.type == "cuda"
                    else nullcontext()
                )
                with amp_context:
                    logits = _forward_with_tta_flag(
                        models,
                        image,
                        mask,
                        dual_scan,
                        conf["deep_supervision"],
                        use_tta=use_tta,
                        mask_prev=mask_prev if dual_scan else None,
                    )
        except RuntimeError as e:
            # Handle tensor size mismatch errors (e.g., odd slice counts that don't divide evenly)
            print(f"RuntimeError during model inference for {names}: {e}")
            print(f"Image shape: {image.shape}, skipping this batch...")
            failed_files.extend(names)
            torch.cuda.empty_cache()
            gc.collect()
            continue

        proba = torch.nn.functional.softmax(logits, dim=1)

        torch.cuda.empty_cache()
        gc.collect()

        logits = torch.sigmoid(logits)
        logits = (logits > 0.5).float()

        if conf["postprocess"]:
            assert (
                conf["min_size"] is not None and conf["distance_threshold"] is not None
            ), "Postprocessing is enabled but min_size and distance_threshold are not set in the config."
            for i, (logit, prob) in enumerate(zip(logits, proba)):
                logit = rearrange(logit, "c d h w -> h w d c")
                prob = rearrange(prob, "c d h w -> h w d c")
                logit = postprocess_segmentation(
                    logit,
                    min_size=conf["min_size"],
                    distance_threshold=conf["distance_threshold"],
                )
                logits[i] = rearrange(logit, "h w d c -> c d h w")
                proba[i] = rearrange(prob, "h w d c -> c d h w")

        try:
            reconstruction_mask_shapes = (
                [(mask.shape[1], *tuple(shape[1:])) for shape in mask_shape]
                if dual_scan
                else mask_shape
            )
            n_logits, n_proba, n_img, n_masks = recreate_image(
                len(names),
                rearrange(logits, "b c d h w -> b c h w d"),
                rearrange(proba, "b c d h w -> b c h w d"),
                rearrange(image, "b c d h w -> b c h w d"),
                rearrange(mask, "b c d h w -> b c h w d"),
                im_shape,
                reconstruction_mask_shapes,
                coords,
                patches_per_image,
            )
        except Exception as e:
            print(f"Error in recreating image: {e}")
            print(f"Failed files: {names}")
            failed_files.extend(names)  # Track failed files
            continue  # Skip this batch and move to the next one

        for (
            name,
            logit,
            prob,
            img,
            mask,
            m_nii,
            im_nii,
            orig_shape,
            orig_spacing,
            orig_orientation,
        ) in zip(
            names,
            n_logits,
            n_proba,
            n_img,
            n_masks,
            mask_nii,
            img_nii,
            original_shapes,
            original_spacings,
            original_orientations,
        ):
            # (batch, channels, height, width, depth) -> (height, width, depth)
            if dual_scan:
                img = img[0]

            metadata = get_resampling_metadata(im_nii)

            # Remove depth padding if applied during preprocessing
            # logit/prob shape: (C, H, W, D), img/mask shape: (C, H, W, D) or (H, W, D)
            if (
                metadata.get("depth_pad_before", 0) > 0
                or metadata.get("depth_pad_after", 0) > 0
            ):
                logit = unpad_depth(logit, metadata)
                prob = unpad_depth(prob, metadata)
                img = unpad_depth(img, metadata)
                mask = unpad_depth(mask, metadata)
            orig_spacing = metadata["original_spacing"]
            orig_affine = metadata["original_affine"]
            orig_orientation = (
                f"{orig_orientation[0]}{orig_orientation[1]}{orig_orientation[2]}"
            )

            img = img.squeeze(0)

            # will save at cropped dimensions
            prob_pz_nifti = nib.Nifti1Image(
                np.float32(prob[1]), im_nii.affine, im_nii.header
            )
            prob_pz_nifti.header.set_zooms(im_nii.header.get_zooms()[1:])
            # add header.extensions
            prob_pz_nifti.header.extensions = m_nii.header.extensions
            prob_pz_nifti.set_data_dtype(np.float32)

            prob_tz_nifti = nib.Nifti1Image(
                np.float32(prob[2]), im_nii.affine, im_nii.header
            )
            prob_tz_nifti.header.set_zooms(im_nii.header.get_zooms()[1:])
            prob_tz_nifti.header.extensions = m_nii.header.extensions
            prob_tz_nifti.set_data_dtype(np.float32)

            # will resample to original space and save
            resampled_prob_pz = resample_mask_to_original_space(
                prob_pz_nifti, "bilinear"
            )
            resampled_prob_pz_data = resampled_prob_pz.get_fdata().astype(np.float32)
            resampled_prob_pz_nifti = nib.Nifti1Image(
                resampled_prob_pz_data, orig_affine
            )
            resampled_prob_pz_nifti.header.set_zooms(orig_spacing)
            resampled_prob_pz_nifti = convert_orientation(
                resampled_prob_pz_nifti, orig_orientation
            )

            resampled_prob_tz = resample_mask_to_original_space(
                prob_tz_nifti, "bilinear"
            )
            resampled_prob_tz_data = resampled_prob_tz.get_fdata().astype(np.float32)
            resampled_prob_tz_nifti = nib.Nifti1Image(
                resampled_prob_tz_data, orig_affine
            )
            resampled_prob_tz_nifti.header.set_zooms(orig_spacing)
            resampled_prob_tz_nifti = convert_orientation(
                resampled_prob_tz_nifti, orig_orientation
            )

            # convert multilabel to multiclass
            pz = logit[1]
            pz = np.where(pz > 0.5, 1, 0)

            tz = logit[2]
            tz = np.where(tz > 0.5, 2, 0)

            pz_tz = pz + tz
            pz_tz = np.clip(pz_tz, 0, 2)
            pz_tz[pz_tz < 0.5] = 0
            pz_tz = np.round(pz_tz)

            pred_pz_tz_nifti = nib.Nifti1Image(
                np.uint16(pz_tz), im_nii.affine, im_nii.header
            )
            pred_pz_tz_nifti.header.set_zooms(im_nii.header.get_zooms()[1:])
            pred_pz_tz_nifti.header.extensions = m_nii.header.extensions
            pred_pz_tz_nifti.set_data_dtype(np.uint16)

            pred_wp_nifti = nib.Nifti1Image(
                np.uint16(np.where(logit[0] > 0.5, 1, 0)), im_nii.affine, im_nii.header
            )
            pred_wp_nifti.header.set_zooms(im_nii.header.get_zooms()[1:])
            pred_wp_nifti.header.extensions = m_nii.header.extensions
            pred_wp_nifti.set_data_dtype(np.uint16)

            resampled_pred_wp = resample_mask_to_original_space(
                pred_wp_nifti, "nearest"
            )
            resampled_pred_wp_data = np.round(resampled_pred_wp.get_fdata()).astype(
                np.uint16
            )
            resampled_pred_wp_nifti = nib.Nifti1Image(
                resampled_pred_wp_data, orig_affine
            )
            resampled_pred_wp_nifti.header.set_zooms(orig_spacing)
            resampled_pred_wp_nifti = convert_orientation(
                resampled_pred_wp_nifti, orig_orientation
            )

            resampled_pred_pz_tz = resample_mask_to_original_space(
                pred_pz_tz_nifti, "nearest"
            )
            resampled_pred_pz_tz_data = np.round(
                resampled_pred_pz_tz.get_fdata()
            ).astype(np.uint16)
            resampled_pred_pz_tz_nifti = nib.Nifti1Image(
                resampled_pred_pz_tz_data, orig_affine
            )
            resampled_pred_pz_tz_nifti.header.set_zooms(orig_spacing)
            resampled_pred_pz_tz_nifti = convert_orientation(
                resampled_pred_pz_tz_nifti, orig_orientation
            )

            img_nifti = nib.Nifti1Image(img, im_nii.affine, im_nii.header)
            img_nifti.header.set_zooms(im_nii.header.get_zooms()[1:])
            img_nifti.header.extensions = im_nii.header.extensions

            m_wp = mask[0]
            m_wp = np.where(m_wp > 0.5, 1, 0)

            m_pz = mask[1]
            m_pz = np.where(m_pz > 0.5, 1, 0)

            m_tz = mask[2]
            m_tz = np.where(m_tz > 0.5, 2, 0)

            mask_combined = m_pz + m_tz
            mask_combined = np.clip(mask_combined, 0, 2)
            mask_combined[mask_combined < 0.5] = 0
            mask_combined = np.round(mask_combined)

            mask_wp_nifti = nib.Nifti1Image(np.uint16(m_wp), m_nii.affine, m_nii.header)
            mask_wp_nifti.header.set_zooms(m_nii.header.get_zooms()[1:])
            mask_wp_nifti.header.extensions = m_nii.header.extensions
            mask_wp_nifti.set_data_dtype(np.uint16)

            mask_pz_tz_nifti = nib.Nifti1Image(
                np.uint16(mask_combined), m_nii.affine, m_nii.header
            )
            mask_pz_tz_nifti.header.set_zooms(m_nii.header.get_zooms()[1:])
            mask_pz_tz_nifti.header.extensions = m_nii.header.extensions
            mask_pz_tz_nifti.set_data_dtype(np.uint16)

            resampled_wp = resample_mask_to_original_space(mask_wp_nifti, "nearest")
            resampled_wp_data = np.round(resampled_wp.get_fdata()).astype(np.uint16)
            resampled_wp_nifti = nib.Nifti1Image(resampled_wp_data, orig_affine)
            resampled_wp_nifti.header.set_zooms(orig_spacing)
            resampled_wp_nifti = convert_orientation(
                resampled_wp_nifti, orig_orientation
            )

            resampled_pz_tz = resample_mask_to_original_space(
                mask_pz_tz_nifti, "nearest"
            )
            resampled_pz_tz_data = np.round(resampled_pz_tz.get_fdata()).astype(
                np.uint16
            )
            resampled_pz_tz_nifti = nib.Nifti1Image(resampled_pz_tz_data, orig_affine)
            resampled_pz_tz_nifti.header.set_zooms(orig_spacing)
            resampled_pz_tz_nifti = convert_orientation(
                resampled_pz_tz_nifti, orig_orientation
            )

            img_nifti.to_filename(f"{pred_dir}/images/{name}.nii.gz")
            mask_pz_tz_nifti.to_filename(f"{pred_dir}/masks/{name}.nii.gz")
            mask_wp_nifti.to_filename(f"{pred_dir}/masks/{name}_wp.nii.gz")

            pred_wp_nifti.to_filename(f"{pred_dir}/preds/wp/{name}.nii.gz")
            pred_pz_tz_nifti.to_filename(f"{pred_dir}/preds/pz_tz/{name}.nii.gz")
            prob_tz_nifti.to_filename(f"{prob_maps_dir}/tz/{name}.nii.gz")
            prob_pz_nifti.to_filename(f"{prob_maps_dir}/pz/{name}.nii.gz")

            # save resampled to disk
            resampled_wp_nifti.to_filename(
                f"{pred_dir}/resampled/masks/wp/{name}.nii.gz"
            )
            resampled_pz_tz_nifti.to_filename(
                f"{pred_dir}/resampled/masks/pz_tz/{name}.nii.gz"
            )

            resampled_pred_wp_nifti.to_filename(
                f"{pred_dir}/resampled/preds/wp/{name}.nii.gz"
            )
            resampled_pred_pz_tz_nifti.to_filename(
                f"{pred_dir}/resampled/preds/pz_tz/{name}.nii.gz"
            )
            resampled_prob_tz_nifti.to_filename(
                f"{prob_maps_dir}/resampled/tz/{name}.nii.gz"
            )
            resampled_prob_pz_nifti.to_filename(
                f"{prob_maps_dir}/resampled/pz/{name}.nii.gz"
            )

    if log_preds_to_wandb:
        pz_artifact.add_dir(f"{pred_dir}/preds/pz_tz")
        wp_artifact.add_dir(f"{pred_dir}/preds/wp")
        wandb.log_artifact(pz_artifact)
        wandb.log_artifact(wp_artifact)

    if skipped_files:
        print(f"\n{'='*60}")
        print(f"INFO: Skipped {len(skipped_files)} files (already exist)")
        print(f"{'='*60}\n")

    if abnormal_files:
        abnormal_files_path = f"{pred_dir}/abnormal_orientation_files.txt"
        with open(abnormal_files_path, "w") as f:
            f.write(
                f"# Files skipped due to abnormal orientation ({len(abnormal_files)} total)\n"
            )
            f.write(
                f"# These files have thickest slice spacing in dimension 0 or 1 instead of 2\n"
            )
            for fname in abnormal_files:
                f.write(f"{fname}\n")
        print(f"\n{'='*60}")
        print(f"INFO: Skipped {len(abnormal_files)} files with abnormal orientations")
        print(f"Abnormal files saved to: {abnormal_files_path}")
        print(f"{'='*60}\n")

    if failed_files:
        failed_files_path = f"{pred_dir}/failed_files.txt"
        with open(failed_files_path, "w") as f:
            f.write(f"# Failed files during inference ({len(failed_files)} total)\n")
            for fname in failed_files:
                f.write(f"{fname}\n")
        print(f"\n{'='*60}")
        print(f"WARNING: {len(failed_files)} files failed during inference!")
        print(f"Failed files saved to: {failed_files_path}")
        print(f"Failed files: {failed_files}")
        print(f"{'='*60}\n")
    else:
        print(f"\nAll files processed successfully!")
