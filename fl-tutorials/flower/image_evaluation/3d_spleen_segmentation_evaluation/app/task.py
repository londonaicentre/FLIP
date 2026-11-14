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

"""Evaluation utilities for the MONAI Flower app."""

import logging

import torch
from monai.data import DataLoader
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

    Discretization mirrors the reference validator (trainer.py:79-81):
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
