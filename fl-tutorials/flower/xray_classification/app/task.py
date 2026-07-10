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

"""Single-epoch train and validate helpers for chest-X-ray classification."""

from logging import INFO

import numpy as np
import torch
from flwr.common import log
from monai.data import DataLoader

from app.data_loading import LesionDict, get_lesion_label
from app.loss_and_metrics import compute_precision_recall_f1, get_bce_loss


def _empty_metrics(lesions: LesionDict) -> dict:
    """Return a fresh per-lesion metrics accumulator for a single pass."""
    return {
        "loss": [],
        "precision": {name: [] for name in lesions.get_lesion_list()},
        "recall": {name: [] for name in lesions.get_lesion_list()},
        "f1-score": {name: [] for name in lesions.get_lesion_list()},
    }


def _aggregate(metrics: dict, lesions: LesionDict) -> dict:
    """Reduce per-batch metric lists to per-lesion scalars via nan-mean."""
    out = {"loss": float(np.nanmean(metrics["loss"])) if metrics["loss"] else float("nan")}
    for kind in ("precision", "recall", "f1-score"):
        for name in lesions.get_lesion_list():
            values = metrics[kind][name]
            out[f"{kind}-{name}"] = float(np.nanmean(values)) if values else float("nan")
    return out


def train_func(
    model: torch.nn.Module,
    train_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    lesions: LesionDict,
) -> dict:
    """Run one epoch of multi-label BCE training; return loss + per-lesion P/R/F1 means."""
    model.train()
    metrics = _empty_metrics(lesions)
    log(INFO, f"Starting training pass on device {device}")

    for i, batch in enumerate(train_loader):
        images = batch["image"].to(device)
        labels = get_lesion_label(batch, lesions).to(device)

        optimizer.zero_grad()
        logits = model(images)
        loss = get_bce_loss(logits, labels)

        # Skip a non-finite batch instead of letting it poison the model: a single
        # NaN/Inf loss backpropagates into every weight via optimizer.step(), after
        # which every subsequent batch is NaN and the whole pass reports loss=nan,
        # flipping the model to ERROR (FLIP#764). Dropping the batch keeps training
        # alive so a transient bad batch can't kill the run.
        if not torch.isfinite(loss):
            log(INFO, f"Skipping batch {i + 1}/{len(train_loader)}: non-finite loss ({loss.item()})")
            continue

        loss.backward()
        # Gradient clipping bounds the update so an exploding gradient on one batch
        # can't diverge the model to NaN within a single optimizer step.
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        metrics["loss"].append(loss.item())
        probs = torch.sigmoid(logits)
        for lesion in lesions.items:
            precision, recall, f1 = compute_precision_recall_f1(probs, labels, lesion.id)
            metrics["precision"][lesion.lesion].append(precision)
            metrics["recall"][lesion.lesion].append(recall)
            metrics["f1-score"][lesion.lesion].append(f1)

        if i % 10 == 0:
            log(INFO, f"Train batch {i + 1}/{len(train_loader)} - loss={loss.item():.4f}")

    aggregated = _aggregate(metrics, lesions)
    log(INFO, f"Training pass complete. loss={aggregated['loss']:.4f}")
    return aggregated


def validate_func(
    model: torch.nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    lesions: LesionDict,
) -> dict:
    """Run one validation pass; return loss + per-lesion P/R/F1 means."""
    model.eval()
    metrics = _empty_metrics(lesions)
    log(INFO, f"Starting validation on {len(val_loader)} batches")

    if len(val_loader) == 0:
        log(INFO, "Validation loader is empty, skipping validation")
        empty_metrics: dict = {"loss": float("nan")}
        for name in lesions.get_lesion_list():
            for kind in ("precision", "recall", "f1-score"):
                empty_metrics[f"{kind}-{name}"] = float("nan")
        return empty_metrics

    with torch.no_grad():
        for i, batch in enumerate(val_loader):
            images = batch["image"].to(device)
            labels = get_lesion_label(batch, lesions).to(device)
            logits = model(images)
            loss = get_bce_loss(logits, labels)

            metrics["loss"].append(loss.item())
            probs = torch.sigmoid(logits)
            for lesion in lesions.items:
                precision, recall, f1 = compute_precision_recall_f1(probs, labels, lesion.id)
                metrics["precision"][lesion.lesion].append(precision)
                metrics["recall"][lesion.lesion].append(recall)
                metrics["f1-score"][lesion.lesion].append(f1)

            if i % 10 == 0:
                log(INFO, f"Val batch {i + 1}/{len(val_loader)} - loss={loss.item():.4f}")

    aggregated = _aggregate(metrics, lesions)
    log(INFO, f"Validation complete. loss={aggregated['loss']:.4f}")
    return aggregated
