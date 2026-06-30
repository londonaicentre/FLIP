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

"""Evaluation metrics for chest X-ray model evaluation.

Provides AUROC computation and label-mapping from pre-trained head
outputs to target labels.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score


def compute_auroc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Return AUROC, or NaN if only one class is present in y_true."""
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_score))


def apply_label_mapping(
    preds: np.ndarray,
    source_labels: dict[str, int],
    mapping: dict[str, tuple[str, str | list[str]]],
    decaf_labels: list[str],
) -> dict[str, np.ndarray]:
    """Map pre-trained head outputs to target labels using a registry entry.

    Args:
        preds: [num_samples, N] array of sigmoid predictions.
        source_labels: Dict mapping source label name → column index.
        mapping: Dict mapping target label name → (method, source).
        decaf_labels: Ordered list of target label names to extract.

    Returns:
        Dict mapping each target label -> [num_samples] prediction array.
    """
    result: dict[str, np.ndarray] = {}
    for decaf_label in decaf_labels:
        if decaf_label not in mapping:
            raise KeyError(
                f"Target label {decaf_label!r} has no entry in the mapping. Available keys: {list(mapping.keys())}"
            )
        method, source = mapping[decaf_label]
        if method == "direct":
            if not isinstance(source, str):
                raise TypeError(
                    f"Expected str source for 'direct' mapping of {decaf_label!r}, got {type(source).__name__}"
                )
            if source not in source_labels:
                raise KeyError(
                    f"Source label {source!r} for target {decaf_label!r} "
                    f"not found in source_labels. "
                    f"Available: {list(source_labels.keys())}"
                )
            result[decaf_label] = preds[..., source_labels[source]]
        elif method == "max":
            if not isinstance(source, list):
                raise TypeError(
                    f"Expected list source for 'max' mapping of {decaf_label!r}, got {type(source).__name__}"
                )
            indices = []
            for src in source:
                if src not in source_labels:
                    raise KeyError(
                        f"Source label {src!r} for target {decaf_label!r} "
                        f"not found in source_labels. "
                        f"Available: {list(source_labels.keys())}"
                    )
                indices.append(source_labels[src])
            result[decaf_label] = np.maximum.reduce([preds[..., i] for i in indices])
        else:
            raise ValueError(
                f"Unknown mapping method {method!r} for target {decaf_label!r}. Expected 'direct' or 'max'."
            )
    return result
