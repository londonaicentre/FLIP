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

import numpy as np
import torch
from data_utils import LesionDict


def get_bce_loss(outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """
    Computes the Binary Cross-Entropy loss, not taking into account elements where the ground truths are -1.
    """

    loss_function = torch.nn.BCEWithLogitsLoss(reduction="none")
    mask = (targets != -1).to(dtype=targets.dtype, device=targets.device)
    loss = loss_function(outputs, targets) * mask
    loss = loss.sum() / mask.sum()
    return loss


def compute_precision_recall_f1(
    prediction: torch.Tensor, ground_truth: torch.Tensor, pathology: str, lesions: LesionDict
):
    prediction = prediction.detach().cpu().numpy().round()
    ground_truth = ground_truth.detach().cpu().numpy()

    # The pathology's column (first match); an unknown pathology gives empty arrays and NaN metrics.
    ids = [int(lesion.id) for lesion in lesions.items if lesion.lesion == pathology][:1]
    prediction_new = prediction[:, ids].ravel()
    ground_truth_new = ground_truth[:, ids].ravel()

    # Calculate metrics using the pathology-specific predictions and ground truth
    tp = np.sum((prediction_new == 1) & (ground_truth_new == 1))
    fp = np.sum((prediction_new == 1) & (ground_truth_new == 0))
    fn = np.sum((prediction_new == 0) & (ground_truth_new == 1))

    # Gracefully handle division by zero cases by returning NaN
    # The trainer will use np.nanmean() to ignore these when computing epoch averages
    # This ensures only valid batches contribute to the metric, not artificially lowering it with 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else np.nan

    return precision, recall, f1
