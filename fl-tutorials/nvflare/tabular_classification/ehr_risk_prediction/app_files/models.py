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

"""Model factory for the EHR risk-prediction tutorial.

Kept byte-identical between the NVFLARE and Flower copies (each sits next to its own
``config.json``) — drift-checked by ``scripts/check_tutorial_sync.sh``.
"""

from __future__ import annotations

import json
from pathlib import Path

from torch import nn


def _n_features_from_config() -> int:
    # Sizing the input layer from config.json's FEATURES list keeps the model and the
    # feature engineering from drifting apart: adding or removing a feature is a one-place
    # config change, and both frameworks call get_model() with no arguments (NVFLARE's
    # PTFileModelPersistor references "models.get_model" directly).
    config_path = Path(__file__).resolve().parent / "config.json"
    with open(config_path) as fh:
        return len(json.load(fh)["FEATURES"])


def get_model(n_features: int | None = None) -> nn.Module:
    """A small MLP risk model — one hidden layer over the tabular features, logits out.

    Args:
        n_features (int | None): Input width. Defaults to the length of ``config.json``'s
            ``FEATURES`` list.

    Returns:
        nn.Module: ``[N, n_features] -> [N, 1]`` logits; apply sigmoid (or use
        ``BCEWithLogitsLoss``) downstream.
    """
    if n_features is None:
        n_features = _n_features_from_config()
    # One hidden layer of 32 units (~350 parameters) — effectively a small, regularised
    # logistic regression with a non-linearity. 32 units + 8 local epochs / 5 global rounds
    # reach ~0.90 held-out AUROC on the Synthea T2DM cohort; a narrower 16-unit layer plateaus
    # markedly lower. Both backends build this from the same factory, so the wire contract
    # (state-dict shapes) stays identical.
    return nn.Sequential(
        nn.Linear(n_features, 32),
        nn.ReLU(),
        nn.Dropout(0.2),
        nn.Linear(32, 1),
    )
