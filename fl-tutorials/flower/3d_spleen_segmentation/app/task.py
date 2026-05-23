# Copyright (c) 2026 Flower Labs GmbH
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

"""Training utilities for the MONAI Flower app."""

from logging import INFO, WARNING

import torch
from flwr.common import log
from monai.data import DataLoader
from monai.losses import DiceCELoss
from monai.metrics import DiceMetric
from monai.networks.utils import one_hot
from monai.transforms import AsDiscrete

from app.transforms import get_sliding_window_inferer


def train_func(
    model: torch.nn.Module,
    train_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    loss_fn: DiceCELoss,
    device: torch.device,
) -> float:
    """Train the model for one pass through the dataset.

    Args:
        model: The segmentation model (UNet) to train
        train_loader: DataLoader with training data
        optimizer: Adam optimizer
        loss_fn: DiceCELoss with to_onehot_y=True, softmax=True (matches flip-fl-base reference)
        device: Device to train on (cuda or cpu)

    Returns:
        Average training loss over the dataset
    """
    model.train()
    total_loss = 0.0
    total_samples = 0

    log(INFO, f"Starting training pass on device {device}")

    for i, batch in enumerate(train_loader):
        images = batch["image"].to(device)
        labels = batch["label"].to(device)

        optimizer.zero_grad()

        predictions = model(images)
        loss = loss_fn(predictions, labels)

        loss.backward()
        optimizer.step()

        batch_loss = loss.cpu().detach().item()
        batch_size = images.shape[0]

        total_loss += batch_loss * batch_size
        total_samples += batch_size

        if i % 10 == 0:
            log(INFO, f"Batch {i + 1}, Loss: {batch_loss:.4f}")

    avg_loss = total_loss / max(1, total_samples)
    log(INFO, f"Training pass completed. Average loss: {avg_loss:.4f}")

    return avg_loss


def validate_func(
    model: torch.nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    loss_fn: DiceCELoss,
) -> tuple[float, float]:
    """Validate the model using sliding window inference.

    Key features:
    - Sliding window inference for full volume prediction
    - DiceMetric with mean reduction
    - One-hot encoding of labels for Dice computation

    Args:
        model: The segmentation model to evaluate
        val_loader: DataLoader with validation data
        device: Device to evaluate on
        loss_fn: DiceCELoss (not used for validation but included for consistency)

    Returns:
        Mean Dice score across all validation samples
        Running loss across all validation samples
    """
    model.eval()
    # include_background=False + AsDiscrete(argmax=True) below mirror the flip-fl-base
    # reference's validator (trainer.py:79-81). Without these, DiceMetric receives
    # raw logits and the bg dice ~= 1.0 dominates the mean, so val_dice stays pinned
    # at ~0.5 across all rounds regardless of whether the model is actually learning.
    dice_metric = DiceMetric(include_background=False, reduction="mean")

    log(INFO, f"Starting validation on {len(val_loader)} batches")

    if len(val_loader) == 0:
        log(WARNING, "Validation loader is empty, skipping validation")
        return -1, -1

    running_loss = 0.0
    inferer = get_sliding_window_inferer(sw_device=device)

    with torch.no_grad():
        for i, batch in enumerate(val_loader):
            images = batch["image"].to(device)
            labels = batch["label"].to(device)

            predictions = inferer(inputs=images, network=model)

            loss = loss_fn(predictions, labels).item()
            running_loss += loss

            num_classes = predictions.shape[1]
            post_pred = AsDiscrete(argmax=True, to_onehot=num_classes)
            predictions_onehot = torch.stack([post_pred(p) for p in predictions])
            labels_one_hot = one_hot(labels, num_classes=num_classes)

            dice_metric(predictions_onehot, labels_one_hot)

            log(INFO, f"Validation batch {i + 1}/{len(val_loader)} processed")

    # Compute final aggregated Dice score (foreground only — spleen for this task).
    dice_score = dice_metric.aggregate().cpu().numpy().item()
    running_loss /= max(1, len(val_loader.dataset))
    dice_metric.reset()

    log(INFO, f"Validation completed. Foreground Dice score: {dice_score:.4f}, Average Loss: {running_loss:.4f}")

    return dice_score, running_loss


def test_func(
    model: torch.nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    loss_fn: DiceCELoss,
) -> tuple[float, float]:
    """Test the model on test data (same as validate_func but for test set).

    Args:
        model: The segmentation model to evaluate
        val_loader: DataLoader with test data
        device: Device to evaluate on
        loss_fn: DiceCELoss for computing loss

    Returns:
        Mean Dice score across all test samples
        Running loss across all test samples
    """
    model.eval()
    # Same discretization fix as validate_func above (see comment there).
    dice_metric = DiceMetric(include_background=False, reduction="mean")

    log(INFO, f"Starting test evaluation on {len(val_loader)} batches")

    if len(val_loader) == 0:
        log(WARNING, "Test loader is empty, skipping evaluation")
        return -1, -1

    running_loss = 0.0
    with torch.no_grad():
        for i, batch in enumerate(val_loader):
            images = batch["image"].to(device)
            labels = batch["label"].to(device)
            predictions = model(images)
            loss = loss_fn(predictions, labels).item()
            running_loss += loss

            num_classes = predictions.shape[1]
            post_pred = AsDiscrete(argmax=True, to_onehot=num_classes)
            predictions_onehot = torch.stack([post_pred(p) for p in predictions])
            labels_one_hot = one_hot(labels, num_classes=num_classes)

            dice_metric(predictions_onehot, labels_one_hot)

            log(INFO, f"Test batch {i + 1}/{len(val_loader)} processed")

    # Foreground-only dice (spleen for this task).
    dice_score = dice_metric.aggregate().cpu().numpy().item()
    running_loss /= max(1, len(val_loader.dataset))
    dice_metric.reset()

    log(INFO, f"Test evaluation completed. Foreground Dice score: {dice_score:.4f}, Average Loss: {running_loss:.4f}")

    return dice_score, running_loss
