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

"""Ark+ model factory for the FLIP Client-API evaluation pipeline.

Provides:
- ``get_model()`` — build the single Ark+ model for this job. Referenced by ``FlipEvalRecipe``'s
  ``PTFileModelPersistor(model={"path": "models.get_model"})`` (server) and by the evaluator (client).
- ``_build_arkplus_raw(config)`` — build an ArkSwinTransformer from a config dict (also used by
  ``process_tools/preprocess_checkpoints.py``).

Unlike the legacy executor tutorial, there is no ``model_paths`` dict: the Client-API server locator
(``EvaluationModelLocator``) loads the uploaded checkpoint directly and broadcasts the weights over the
``validate`` task, so only a single-model getter is needed here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import arkplus_flat_models
from torch import nn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
# Model construction only — no pretrained weights are loaded here.
# This is the construction half of arkplus_flat_models.build_omni_model,
# used by preprocess_checkpoints.py to create a bare ArkSwinTransformer
# for checkpoint validation and cleaning.
def _build_arkplus_raw(arkplus_config: dict) -> nn.Module:
    """Return a raw ArkSwinTransformer matching *arkplus_config*."""
    model_name = str(arkplus_config.get("MODEL_NAME", "swin_large_768"))
    input_size = int(arkplus_config.get("INPUT_SIZE", 768))
    projector_features = arkplus_config.get("PROJECTOR_FEATURES", 1376)
    use_mlp = bool(arkplus_config.get("USE_MLP", False))
    num_classes_list = list(arkplus_config.get("NUM_CLASSES_LIST", [5]))

    if model_name in ("swin_large_768", "swin_large_384"):
        return arkplus_flat_models.ArkSwinTransformer(
            num_classes_list,
            projector_features,
            use_mlp,
            img_size=input_size,
            patch_size=4,
            window_size=12,
            embed_dim=192,
            depths=(2, 2, 18, 2),
            num_heads=(6, 12, 24, 48),
        )
    if model_name == "swin_base":
        return arkplus_flat_models.ArkSwinTransformer(
            num_classes_list,
            projector_features,
            use_mlp,
            patch_size=4,
            window_size=7,
            embed_dim=128,
            depths=(2, 2, 18, 2),
            num_heads=(4, 8, 16, 32),
        )
    raise ValueError(f"Unknown ARKPLUS model_name: {model_name!r}")


# ---------------------------------------------------------------------------
# get_model — used by the recipe's PTFileModelPersistor (server) and evaluator (client)
# ---------------------------------------------------------------------------
def get_model() -> nn.Module:
    """Build the single Ark+ model for this evaluation job.

    Reads the single ``config.json`` ``models`` entry and builds its ``ArkSwinTransformer`` via
    ``_build_arkplus_raw``. No weights are loaded here — the server loads the uploaded checkpoint and
    broadcasts the weights to the client over the ``validate`` task.
    """
    cfg_path = Path(__file__).resolve().parent / "config.json"
    with open(cfg_path) as f:
        cfg = json.load(f)

    models_cfg = cfg.get("models", {})
    if len(models_cfg) != 1:
        raise ValueError(
            f"Baseline evaluation expects exactly one model in config.json['models']; got {len(models_cfg)}."
        )
    ((model_name, model_info),) = models_cfg.items()
    ark_cfg = model_info["arkplus_config"]
    logger.info("Building model %s (NUM_CLASSES_LIST=%s)", model_name, ark_cfg.get("NUM_CLASSES_LIST"))
    return _build_arkplus_raw(ark_cfg)
