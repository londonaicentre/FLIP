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

import logging

import torch
from monai.data import DataLoader
from monai.losses import DiceLoss
from monai.metrics import DiceMetric
from monai.networks.utils import one_hot

from app.transforms import get_sliding_window_inferer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def train_func(
    model: torch.nn.Module,
    train_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    loss_fn: DiceLoss,
    device: torch.device,
) -> float:
    """Train the model for one pass through the dataset.

    Args:
        model: The segmentation model (UNet) to train
        train_loader: DataLoader with training data
        optimizer: Adam optimizer
        loss_fn: DiceLoss with to_onehot_y=True, softmax=True
        device: Device to train on (cuda or cpu)

    Returns:
        Average training loss over the dataset
    """
    model.train()
    total_loss = 0.0
    total_samples = 0

    logger.info(f"Starting training pass on device {device}")

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
            logger.info(f"Batch {i + 1}, Loss: {batch_loss:.4f}")

    avg_loss = total_loss / max(1, total_samples)
    logger.info(f"Training pass completed. Average loss: {avg_loss:.4f}")

    return avg_loss


def validate_func(
    model: torch.nn.Module,
    val_loader: DataLoader,
    device: torch.device,
    loss_fn: DiceLoss,
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
        loss_fn: DiceLoss (not used for validation but included for consistency)

    Returns:
        Mean Dice score across all validation samples
        Running loss across all validation samples
    """
    model.eval()
    dice_metric = DiceMetric(reduction="mean")

    logger.info(f"Starting validation on {len(val_loader)} batches")

    if len(val_loader) == 0:
        logger.warning("Validation loader is empty, skipping validation")
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

            # Convert labels to one-hot encoding for Dice computation
            # (same approach as FLARE validator)
            num_classes = predictions.shape[1]
            labels_one_hot = one_hot(labels, num_classes=num_classes)

            # Accumulate Dice scores
            dice_metric(predictions, labels_one_hot)

            logger.info(f"Validation batch {i + 1}/{len(val_loader)} processed")

    # Compute final aggregated Dice score
    dice_score = dice_metric.aggregate().cpu().numpy().item()
    running_loss /= max(1, len(val_loader.dataset))
    dice_metric.reset()

    logger.info(f"Validation completed. Mean Dice score: {dice_score:.4f}, Average Loss: {running_loss:.4f}")

    return dice_score, running_loss
