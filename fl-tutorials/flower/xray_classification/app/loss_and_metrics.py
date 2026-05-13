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

"""Loss and per-lesion metrics for chest-X-ray multi-label classification."""

import numpy as np
import torch


def get_bce_loss(outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """Compute Binary Cross-Entropy with logits, ignoring -1 (masked/unknown) targets."""
    loss_function = torch.nn.BCEWithLogitsLoss(reduction="none")
    mask = (targets != -1).to(dtype=targets.dtype, device=targets.device)
    loss = loss_function(outputs, targets) * mask
    return loss.sum() / mask.sum()


def compute_precision_recall_f1(
    prediction: torch.Tensor, ground_truth: torch.Tensor, lesion_id: int
) -> tuple[float, float, float]:
    """Per-lesion precision/recall/F1, returning NaN when the denominator is zero.

    The trainer averages metrics across batches with ``np.nanmean`` so empty-class
    batches don't artificially deflate the epoch score.
    """
    pred_col = prediction.detach().cpu().numpy().round()[:, lesion_id]
    gt_col = ground_truth.detach().cpu().numpy()[:, lesion_id]

    tp = np.sum((pred_col == 1) & (gt_col == 1))
    fp = np.sum((pred_col == 1) & (gt_col == 0))
    fn = np.sum((pred_col == 0) & (gt_col == 1))

    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else np.nan

    return precision, recall, f1
