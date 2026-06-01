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

"""Training and evaluation utilities for the MONAI Flower app."""

import logging

import torch
from monai.data import DataLoader
from monai.losses import DiceLoss
from monai.metrics import DiceMetric, MeanIoU
from monai.networks.utils import one_hot
from monai.transforms import AsDiscrete

from app.transforms import get_sliding_window_inferer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Segmentation metrics computed for every evaluation. Each entry maps a metric
# name — the key that reaches the FLIP Hub and the aggregated MetricRecord — to a
# factory that builds a fresh MONAI metric. All use the DiceMetric-style
# (y_pred, y) one-hot interface with include_background=False (foreground-only),
# so they score the same discretized batch in a single inference sweep.
#
# This is the evaluation app's customisation surface: add a metric here and the
# client computes and returns it automatically — the server's FedAvg strategy
# aggregates whatever keys arrive, so no server or config change is needed.
_METRICS = {
    "mean_dice": lambda: DiceMetric(include_background=False, reduction="mean_batch"),
    "mean_iou": lambda: MeanIoU(include_background=False, reduction="mean_batch"),
}


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


def evaluate_func(
    model: torch.nn.Module,
    test_loader: DataLoader,
    device: torch.device,
) -> dict[str, float]:
    """Evaluate the model and return the mean of each segmentation metric.

    Runs a single sliding-window inference sweep over ``test_loader`` and scores
    every subject with each metric in ``_METRICS``, returning the per-subject
    mean of each. To add or remove a metric, edit ``_METRICS`` — the change flows
    through ``client_app`` into the MetricRecord and is aggregated natively by the
    server, with no server or config change.

    Discretization mirrors the flip-fl-base reference validator (trainer.py:79-81):
      - Sliding-window logits are discretized via AsDiscrete(argmax=True, to_onehot=N)
        before scoring. Without this step the metrics receive raw logits and produce
        a degenerate score that's constant (~0.5 for binary segmentation) regardless
        of model quality.
      - Metrics are built with include_background=False so the returned score
        averages only the foreground class(es); a 2-class binary segmenter where
        the foreground is <1% of voxels otherwise dominates the mean with a
        background score ≈ 1.0 and hides any real signal in the spleen class.

    Args:
        model: The segmentation model to evaluate
        test_loader: DataLoader with test data
        device: Device to evaluate on

    Returns:
        Mapping of metric name to its mean value across the test subjects.
    """
    model.eval()

    logger.info(f"Starting evaluation on {len(test_loader)} batches")

    if len(test_loader) == 0:
        logger.warning("Test loader is empty, skipping evaluation")
        return {name: 0.0 for name in _METRICS}

    # One fresh metric object per requested metric; per-subject scores accumulate
    # into parallel lists so each metric is macro-averaged over subjects.
    metrics = {name: factory() for name, factory in _METRICS.items()}
    per_sample_scores: dict[str, list[float]] = {name: [] for name in _METRICS}

    inferer = get_sliding_window_inferer(sw_device=device)

    with torch.no_grad():
        for i, batch in enumerate(test_loader):
            images = batch["image"].to(device)
            labels = batch["label"].to(device)

            predictions = inferer(inputs=images, network=model)

            # AsDiscrete + one_hot give the metrics the discrete inputs they expect.
            num_classes = predictions.shape[1]
            post_pred = AsDiscrete(argmax=True, to_onehot=num_classes)
            # AsDiscrete drops the batch dim; re-add it so metrics see (B, C, ...).
            predictions_onehot = torch.stack([post_pred(p) for p in predictions])
            labels_one_hot = one_hot(labels, num_classes=num_classes)

            for name, metric in metrics.items():
                metric(predictions_onehot, labels_one_hot)
                # include_background=False already excluded the bg row; mean across
                # the remaining foreground class(es) (just spleen for binary).
                batch_score = float(metric.aggregate().cpu().numpy().mean())
                per_sample_scores[name].append(batch_score)
                metric.reset()

            logger.info(f"Evaluation batch {i + 1}/{len(test_loader)} processed")

    results = {name: (sum(scores) / len(scores) if scores else 0.0) for name, scores in per_sample_scores.items()}
    logger.info(f"Evaluation completed. Mean foreground metrics: {results}")

    return results
