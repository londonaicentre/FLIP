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

"""Single-epoch train and evaluate helpers for EHR risk prediction."""

from logging import INFO, WARNING

import numpy as np
import torch
from flwr.common import log
from torch.utils.data import DataLoader

from app.feature_engineering import binary_accuracy, safe_auroc


def train_func(
    model: torch.nn.Module,
    train_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: torch.nn.Module,
    device: torch.device,
) -> dict:
    """Run one epoch of weighted BCE training; return mean loss + AUROC + accuracy."""
    model.train()
    losses: list[float] = []
    all_labels: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []

    for i, (features, labels) in enumerate(train_loader):
        features, labels = features.to(device), labels.to(device)
        optimizer.zero_grad()
        logits = model(features)
        loss = criterion(logits, labels)

        # Skip a non-finite batch instead of letting it poison the model: a single NaN/Inf
        # loss backpropagates into every weight via optimizer.step(), after which every
        # subsequent batch is NaN (same guard as the imaging tutorials — FLIP#764).
        if not torch.isfinite(loss):
            log(WARNING, f"Skipping batch {i + 1}/{len(train_loader)}: non-finite loss ({loss.item()})")
            continue

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        losses.append(loss.item())
        all_labels.append(labels.detach().cpu().numpy())
        all_probs.append(torch.sigmoid(logits).detach().cpu().numpy())

    return _aggregate(losses, all_labels, all_probs)


def validate_func(
    model: torch.nn.Module,
    loader: DataLoader,
    criterion: torch.nn.Module,
    device: torch.device,
) -> dict:
    """Run one evaluation pass; return mean loss + AUROC + accuracy."""
    model.eval()
    losses: list[float] = []
    all_labels: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []

    if len(loader) == 0:
        log(INFO, "Evaluation loader is empty, skipping pass")
        return {"loss": float("nan"), "auroc": float("nan"), "accuracy": float("nan")}

    with torch.no_grad():
        for features, labels in loader:
            features, labels = features.to(device), labels.to(device)
            logits = model(features)
            loss = criterion(logits, labels)
            if not torch.isfinite(loss):
                log(WARNING, f"Skipping evaluation batch: non-finite loss ({loss.item()})")
                continue
            losses.append(loss.item())
            all_labels.append(labels.detach().cpu().numpy())
            all_probs.append(torch.sigmoid(logits).detach().cpu().numpy())

    return _aggregate(losses, all_labels, all_probs)


def _aggregate(losses: list[float], all_labels: list[np.ndarray], all_probs: list[np.ndarray]) -> dict:
    labels = np.concatenate(all_labels) if all_labels else np.empty(0)
    probs = np.concatenate(all_probs) if all_probs else np.empty(0)
    return {
        "loss": float(np.mean(losses)) if losses else float("nan"),
        # NaN on a single-class split is deliberate and visible: the server-side selector
        # refuses a non-finite round with a warning rather than pinning "best" to it.
        "auroc": safe_auroc(labels, probs),
        "accuracy": binary_accuracy(labels, probs),
    }
