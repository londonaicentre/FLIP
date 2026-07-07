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

"""Client-API trainer for the Ark+ fine-tuning chest X-ray tutorial.

This is the NVFLARE Client-API counterpart of the legacy ``FLIP_TRAINER(Executor)``. The FL transport
is restructured onto the canonical ``flare.init()`` / ``flare.is_running()`` round loop (receive the
global model, train locally, send back a ``DIFF``), while the Ark+ fine-tuning compute — head-only
fine-tuning with a frozen backbone, an EMA teacher/student consistency loss, AMP, and per-lesion
precision/recall/F1 — is preserved verbatim from the executor tutorial. Metrics stream through
``SummaryWriter`` (one series per lesion) instead of the legacy ``send_metrics_value``.
"""

from __future__ import annotations

import argparse
import copy
import gc
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import nvflare.client as flare
import torch
from data_utils import (
    Lesion,
    LesionDict,
    build_datalist,
    get_lesion_label,
    get_xray_transforms,
)
from loss_and_metrics import compute_precision_recall_f1, get_bce_loss
from models import get_model
from monai.data import DataLoader, Dataset
from nvflare.client.tracking import SummaryWriter

logger = logging.getLogger(__name__)


@dataclass
class ArkTrainState:
    """Persistent per-client training state + Ark+ hyperparameters, created once and reused every round."""

    model: torch.nn.Module
    teacher_model: torch.nn.Module | None
    optimizer: torch.optim.Optimizer
    scheduler: torch.optim.lr_scheduler.LRScheduler
    amp_scaler: torch.amp.GradScaler
    consistency_loss: torch.nn.Module
    lesions: LesionDict
    device: torch.device
    use_teacher_student: bool
    ema_mode: str
    teacher_momentum: float
    consistency_weight: float
    amp_enabled: bool
    amp_dtype: torch.dtype


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    NVFlare's TaskScriptRunner does a naive whitespace split on task_script_args,
    so the SQL query (which can contain spaces) is plumbed via the top-level
    ``query`` key in ``config/config_fed_client.json`` rather than as a CLI flag.
    In dev/simulator mode this is ignored by ``flip.get_dataframe``.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script (kept unmutated for build_datalist)."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def build_lesion_dict(config: dict) -> LesionDict:
    """Build the trainable LesionDict from config, dropping the reserved ``-1`` normality key."""
    lesions = dict(config["LESIONS"])
    lesions.pop("-1", None)
    return LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in lesions.items()])


def _autocast(state: ArkTrainState):
    """AMP autocast context — mirrors the legacy trainer so training/validation math is identical."""
    if hasattr(torch, "amp"):
        return torch.amp.autocast(device_type="cuda", dtype=state.amp_dtype, enabled=state.amp_enabled)
    return torch.cuda.amp.autocast(dtype=state.amp_dtype, enabled=state.amp_enabled)


def _setup_teacher_model(
    model: torch.nn.Module,
    device: torch.device,
    use_teacher_student: bool,
    ema_mode: str,
    teacher_momentum: float,
    consistency_weight: float,
) -> tuple[torch.nn.Module | None, bool, str]:
    """Create the local EMA teacher used by Ark+ client training (verbatim from legacy _setup_teacher_model)."""
    if not use_teacher_student:
        return None, False, ema_mode

    if not hasattr(model, "forward_with_features"):
        logger.warning(
            "ARKPLUS.USE_TEACHER_STUDENT=true, but the model does not expose "
            "forward_with_features(); falling back to standard training."
        )
        return None, False, ema_mode

    if ema_mode not in {"epoch", "iteration"}:
        logger.warning("Unsupported ARKPLUS.EMA_MODE=%r; using 'epoch' instead.", ema_mode)
        ema_mode = "epoch"

    if not 0.0 <= consistency_weight <= 1.0:
        raise ValueError("ARKPLUS.CONSISTENCY_WEIGHT must be between 0 and 1.")

    teacher_model = copy.deepcopy(model).to(device)
    teacher_model.eval()
    for param in teacher_model.parameters():
        param.requires_grad_(False)

    logger.info(
        "Ark+ teacher/student training enabled: EMA_MODE=%s, TEACHER_MOMENTUM=%s, CONSISTENCY_WEIGHT=%s",
        ema_mode,
        teacher_momentum,
        consistency_weight,
    )
    return teacher_model, True, ema_mode


def _sync_teacher_from_student_once(state: ArkTrainState, already_synced: bool) -> bool:
    """Initialize the local teacher from the first server-provided student weights (legacy semantics)."""
    if not state.use_teacher_student or state.teacher_model is None or already_synced:
        return already_synced
    state.teacher_model.load_state_dict(state.model.state_dict())
    state.teacher_model.eval()
    return True


def _ema_update_teacher(state: ArkTrainState) -> None:
    """Update the local teacher as an EMA of the student model (verbatim from legacy _ema_update_teacher)."""
    if not state.use_teacher_student or state.teacher_model is None:
        return
    momentum = state.teacher_momentum
    with torch.no_grad():
        student_state = state.model.state_dict()
        teacher_state = state.teacher_model.state_dict()
        for name, teacher_value in teacher_state.items():
            student_value = student_state[name].detach()
            if torch.is_floating_point(teacher_value):
                teacher_value.mul_(momentum).add_(student_value.to(teacher_value.device), alpha=1.0 - momentum)
            else:
                teacher_value.copy_(student_value.to(teacher_value.device))


def _log_batch_distribution(
    labels: torch.Tensor,
    lesions: LesionDict,
    epoch: int,
    phase: str,
    batch_idx: int,
    n_batches: int,
) -> None:
    """Log the per-lesion positive/negative/masked class counts for one batch (preserved from legacy)."""
    labels_np = labels.detach().cpu().numpy()
    batch_info = f"Epoch {epoch + 1}, {phase} Batch {batch_idx + 1}/{n_batches}, Batch size: {labels.shape[0]} - "
    for lesion_idx, lesion in enumerate(lesions.items):
        lesion_labels = labels_np[:, lesion_idx]
        valid_labels = lesion_labels[lesion_labels != -1]
        if len(valid_labels) > 0:
            num_positive = int(np.sum(valid_labels == 1))
            num_negative = int(np.sum(valid_labels == 0))
            batch_info += f"{lesion.lesion}: {num_positive} pos / {num_negative} neg; "
        else:
            batch_info += f"{lesion.lesion}: all masked; "
    logger.info(batch_info)


def log_dataset_class_distribution(train_dict: list, val_dict: list, lesions: LesionDict) -> None:
    """Log the overall class distribution in the train/val datasets (preserved from legacy)."""
    logger.info("\n" + "=" * 80)
    logger.info("OVERALL CLASS DISTRIBUTION IN DATASETS")
    logger.info("=" * 80)
    for dataset_name, datalist in [("TRAINING", train_dict), ("VALIDATION", val_dict)]:
        logger.info(f"\n{dataset_name} Dataset ({len(datalist)} samples):")
        for lesion in lesions.items:
            lesion_name = lesion.lesion
            all_labels = [item[lesion_name] for item in datalist]
            num_positive = sum(1 for label in all_labels if label == 1)
            num_negative = sum(1 for label in all_labels if label == 0)
            num_masked = sum(1 for label in all_labels if label == -1)
            if num_positive + num_negative > 0:
                positive_ratio = num_positive / (num_positive + num_negative) * 100
            else:
                positive_ratio = 0.0
            logger.info(
                f"  {lesion_name:20s}: {num_positive:4d} positive, {num_negative:4d} negative, "
                f"{num_masked:4d} masked/unknown"
            )
            logger.info(f"                        Positive ratio: {positive_ratio:.2f}% (excluding masked)")
    logger.info("=" * 80 + "\n")


def _safe_mean(values: list) -> float:
    if not values:
        return float("nan")
    return float(np.nanmean(values))


def _publish(writer: SummaryWriter, label: str, value: float, step: int) -> None:
    """Push a scalar through SummaryWriter, swapping NaN for 0.0 like the legacy code did."""
    if np.isnan(value):
        logger.warning(f"{label} is NaN — sending 0.0")
        value = 0.0
    writer.add_scalar(label, value, global_step=step)


def aggregate_and_publish(
    train_metrics: dict,
    val_metrics: dict | None,
    writer: SummaryWriter,
    lesions: LesionDict,
    step: int,
) -> None:
    """Aggregate per-batch metrics with np.nanmean and stream them, one SummaryWriter series per lesion."""
    _publish(writer, "TRAIN_LOSS", _safe_mean(train_metrics["loss"]), step)
    # Only publish VAL_* scalars when validation actually ran this epoch (per VALIDATE_EVERY) — emitting a
    # 0.0 placeholder would inject spurious zeros into the validation series.
    if val_metrics is not None:
        _publish(writer, "VAL_LOSS", _safe_mean(val_metrics["loss"]), step)
    for metric in ["f1-score", "precision", "recall"]:
        for name in lesions.get_lesion_list():
            # Include the lesion name in the tag so each lesion writes to a distinct series.
            _publish(writer, f"TRAIN-{metric.upper()}-{name}", _safe_mean(train_metrics[metric][name]), step)
            if val_metrics is not None:
                _publish(writer, f"VAL-{metric.upper()}-{name}", _safe_mean(val_metrics[metric][name]), step)


def _empty_metrics(lesions: LesionDict) -> dict:
    metrics: dict = {"loss": [], "precision": {}, "recall": {}, "f1-score": {}}
    for name in lesions.get_lesion_list():
        metrics["precision"][name] = []
        metrics["recall"][name] = []
        metrics["f1-score"][name] = []
    return metrics


def train_one_epoch(state: ArkTrainState, loader: DataLoader, epoch: int) -> dict:
    """One training pass. Ark+ teacher/student + AMP compute preserved verbatim from legacy local_train."""
    state.model.train()
    if state.teacher_model is not None:
        state.teacher_model.eval()

    metrics = _empty_metrics(state.lesions)
    for i, batch in enumerate(loader):
        images = batch["image"].to(state.device)
        labels = get_lesion_label(batch, state.lesions).to(state.device)
        _log_batch_distribution(labels, state.lesions, epoch, "Train", i, len(loader))

        state.optimizer.zero_grad()
        with _autocast(state):
            if state.use_teacher_student and state.teacher_model is not None:
                student_features, output = state.model.forward_with_features(images)
                with torch.no_grad():
                    teacher_features, _ = state.teacher_model.forward_with_features(images)
                classification_loss = get_bce_loss(output, labels)
                consistency_loss = state.consistency_loss(student_features, teacher_features.detach())
                loss = (
                    1.0 - state.consistency_weight
                ) * classification_loss + state.consistency_weight * consistency_loss
            else:
                output = state.model(images)
                loss = get_bce_loss(output, labels)
        state.amp_scaler.scale(loss).backward()
        state.amp_scaler.step(state.optimizer)
        state.amp_scaler.update()
        if state.ema_mode == "iteration":
            _ema_update_teacher(state)

        metrics["loss"].append(loss.item())
        output = torch.sigmoid(output)
        for pathology in state.lesions.get_lesion_list():
            precision, recall, f1_score = compute_precision_recall_f1(output, labels, pathology, lesions=state.lesions)
            metrics["precision"][pathology].append(precision)
            metrics["recall"][pathology].append(recall)
            metrics["f1-score"][pathology].append(f1_score)

    return metrics


def run_eval_pass(state: ArkTrainState, loader: DataLoader, epoch: int, phase: str, log_batches: bool) -> dict:
    """A no-grad evaluation pass over ``loader``. Shared by in-training validation and is_evaluate scoring."""
    state.model.eval()
    metrics = _empty_metrics(state.lesions)
    with torch.no_grad():
        for i, batch in enumerate(loader):
            images = batch["image"].to(state.device)
            labels = get_lesion_label(batch, state.lesions).to(state.device)
            if log_batches:
                _log_batch_distribution(labels, state.lesions, epoch, phase, i, len(loader))
            with _autocast(state):
                output = state.model(images)
                loss = get_bce_loss(output, labels).item()
            metrics["loss"].append(loss)
            output = torch.sigmoid(output)
            for pathology in state.lesions.get_lesion_list():
                precision, recall, f1_score = compute_precision_recall_f1(
                    output, labels, pathology, lesions=state.lesions
                )
                metrics["precision"][pathology].append(precision)
                metrics["recall"][pathology].append(recall)
                metrics["f1-score"][pathology].append(f1_score)
    return metrics


def local_train(
    state: ArkTrainState,
    train_loader: DataLoader,
    val_loader: DataLoader,
    epochs: int,
    validate_every: int,
    writer: SummaryWriter,
    global_round: int,
) -> int:
    """Run ``epochs`` local epochs and stream metrics. Returns total iteration count (legacy _n_iterations)."""
    n_iterations = 0
    for epoch in range(epochs):
        train_metrics = train_one_epoch(state, train_loader, epoch)
        n_iterations += len(train_metrics["loss"])

        if state.ema_mode == "epoch":
            _ema_update_teacher(state)

        val_metrics = None
        if epoch % validate_every == 0:
            val_metrics = run_eval_pass(state, val_loader, epoch, "Val", log_batches=True)
            state.model.train()

        state.scheduler.step()

        step = global_round * epochs + epoch + 1
        aggregate_and_publish(train_metrics, val_metrics, writer, state.lesions, step)

    return n_iterations


def cross_site_validate(state: ArkTrainState, loader: DataLoader, writer: SummaryWriter) -> dict:
    """Score the global model on the local val split and publish TEST_* metrics (folds in validator.py)."""
    metrics = run_eval_pass(state, loader, epoch=0, phase="Test", log_batches=False)
    writer.add_scalar("TEST_LOSS", _safe_mean(metrics["loss"]), global_step=0)
    for metric in ["f1-score", "precision", "recall"]:
        for name in state.lesions.get_lesion_list():
            # Include the lesion name in the tag so each lesion writes to a distinct series.
            writer.add_scalar(f"TEST-{metric.upper()}-{name}", _safe_mean(metrics[metric][name]), global_step=0)
    return metrics


def main() -> None:
    args = parse_args()
    config = load_config()

    epochs = config["LOCAL_ROUNDS"]
    lr_start = config["LR_START"]
    lr_end = config["LR_END"]
    batch_size = config["BATCH_SIZE"]
    num_workers = int(config.get("DATALOADER_WORKERS", 4))
    validate_every = config["VALIDATE_EVERY"] if "VALIDATE_EVERY" in config else 1

    _value_to_numerical = {int(k): v for k, v in config["value_to_numerical"].items()}
    if 0 not in _value_to_numerical and 1 not in _value_to_numerical:
        raise ValueError("value_to_numerical must contain mappings for 0 and 1.")

    ark_cfg = config.get("ARKPLUS", {})
    use_teacher_student = bool(ark_cfg.get("USE_TEACHER_STUDENT", False))
    ema_mode = str(ark_cfg.get("EMA_MODE", "epoch")).lower()
    teacher_momentum = float(ark_cfg.get("TEACHER_MOMENTUM", 0.9))
    consistency_weight = float(ark_cfg.get("CONSISTENCY_WEIGHT", 0.1))
    amp_enabled = bool(ark_cfg.get("USE_AMP", False)) and torch.cuda.is_available()
    amp_dtype_name = str(ark_cfg.get("AMP_DTYPE", "float16")).lower()
    amp_dtype = torch.bfloat16 if amp_dtype_name == "bfloat16" else torch.float16

    lesions = build_lesion_dict(config)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    model = get_model().to(device)
    # Freeze backbone + projector, only train the classifier head (verbatim from legacy).
    for name, param in model.named_parameters():
        if not name.startswith("ark_model.omni_heads"):
            param.requires_grad = False
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    logger.info(f"Trainable params: {trainable:,} / {total:,} (backbone frozen)")

    consistency_loss = torch.nn.MSELoss()
    try:
        amp_scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled)
    except TypeError:
        amp_scaler = torch.cuda.amp.GradScaler(enabled=amp_enabled)
    teacher_model, use_teacher_student, ema_mode = _setup_teacher_model(
        model, device, use_teacher_student, ema_mode, teacher_momentum, consistency_weight
    )

    # Optimizer trains only the unfrozen head params; the scheduler decays LR_START -> LR_END over one
    # global round's worth of local epochs (LOCAL_ROUNDS). Both are created once and persist across the
    # whole flare.is_running() loop (never reset per round), matching the legacy scheduler lifecycle.
    optimizer = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=lr_start)
    gamma_lr = (lr_end / lr_start) ** (1 / epochs)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=gamma_lr)

    if amp_enabled:
        logger.info(f"Mixed precision enabled for training with dtype={amp_dtype}.")

    state = ArkTrainState(
        model=model,
        teacher_model=teacher_model,
        optimizer=optimizer,
        scheduler=scheduler,
        amp_scaler=amp_scaler,
        consistency_loss=consistency_loss,
        lesions=lesions,
        device=device,
        use_teacher_student=use_teacher_student,
        ema_mode=ema_mode,
        teacher_momentum=teacher_momentum,
        consistency_weight=consistency_weight,
        amp_enabled=amp_enabled,
        amp_dtype=amp_dtype,
    )

    # flare.init() must precede any other flare.* call (e.g. get_site_name()); data loads once, after init.
    flare.init()
    site_name = flare.get_site_name()
    train_dict, val_dict = build_datalist(
        config=config,
        site_name=site_name,
        project_id=args.project_id,
        query=load_query(),
        logger=logger,
    )
    train_loader = DataLoader(
        Dataset(train_dict, transform=get_xray_transforms()),
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=torch.cuda.is_available(),
    )
    val_loader = DataLoader(
        Dataset(val_dict, transform=get_xray_transforms(is_validation=True)),
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=torch.cuda.is_available(),
    )
    logger.info(
        "DataLoader created for site=%s: training batches=%d, validation batches=%d",
        site_name,
        len(train_loader),
        len(val_loader),
    )
    log_dataset_class_distribution(train_dict, val_dict, lesions)

    writer = SummaryWriter()
    teacher_synced = False

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        if flare.is_train():
            original = {k: torch.as_tensor(v) for k, v in input_model.params.items()}
            model.load_state_dict(original)
            teacher_synced = _sync_teacher_from_student_once(state, teacher_synced)
            global_round = input_model.current_round or 0

            n_iterations = local_train(state, train_loader, val_loader, epochs, validate_every, writer, global_round)

            # Send back DIFF (matches the legacy get_model_weights_diff behaviour used with the
            # FullModelShareableGenerator).
            new_state = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            diff = {k: new_state[k] - original[k].detach().cpu().numpy() for k in new_state}
            flare.send(
                flare.FLModel(
                    params=diff,
                    params_type="DIFF",
                    meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
                )
            )

            # Free transient buffers between rounds — the Ark+ backbone + EMA teacher are large.
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        elif flare.is_evaluate():
            model.load_state_dict({k: torch.as_tensor(v) for k, v in input_model.params.items()})
            metrics = cross_site_validate(state, val_loader, writer)
            flare.send(flare.FLModel(metrics={"loss": _safe_mean(metrics["loss"])}))

        elif flare.is_submit_model():
            params = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
            flare.send(flare.FLModel(params=params, params_type="FULL"))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
