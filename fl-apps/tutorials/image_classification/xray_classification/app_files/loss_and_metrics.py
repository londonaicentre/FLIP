# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

    prediction_new = []
    ground_truth_new = []
    for b in range(prediction.shape[0]):
        for lesion in lesions.items:
            if lesion.lesion == pathology:
                prediction_new.append(prediction[b, int(lesion.id)])
                ground_truth_new.append(ground_truth[b, int(lesion.id)])
                break

    prediction_new = np.array(prediction_new)
    ground_truth_new = np.array(ground_truth_new)
    tp = np.sum((prediction == 1) & (ground_truth == 1))
    fp = np.sum((prediction == 1) & (ground_truth == 0))
    fn = np.sum((prediction == 0) & (ground_truth == 1))

    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    f1 = 2 * (precision * recall) / (precision + recall)

    return precision, recall, f1
