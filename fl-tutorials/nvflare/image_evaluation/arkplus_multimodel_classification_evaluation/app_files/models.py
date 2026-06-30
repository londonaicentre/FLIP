"""Ark+ model factory for the FLIP evaluation pipeline.

Provides:
- ``model_paths`` dict — used by the server-side EvaluationPTModelLocator
- ``_build_arkplus_raw(config)`` — build an ArkSwinTransformer from a config dict
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from torch import nn

import arkplus_flat_models

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
            num_classes_list, projector_features, use_mlp,
            img_size=input_size, patch_size=4, window_size=12,
            embed_dim=192, depths=(2, 2, 18, 2),
            num_heads=(6, 12, 24, 48),
        )
    if model_name == "swin_base":
        return arkplus_flat_models.ArkSwinTransformer(
            num_classes_list, projector_features, use_mlp,
            patch_size=4, window_size=7, embed_dim=128,
            depths=(2, 2, 18, 2), num_heads=(4, 8, 16, 32),
        )
    raise ValueError(f"Unknown ARKPLUS model_name: {model_name!r}")


# ---------------------------------------------------------------------------
# model_paths — used by EvaluationPTModelLocator (server-side)
# Keys must match the "path" field in config.json model entries.
# ---------------------------------------------------------------------------
def _build_model_paths() -> dict[str, nn.Module]:
    cfg_path = Path(__file__).resolve().parent / "config.json"
    with open(cfg_path, "r") as f:
        cfg = json.load(f)
    paths: dict[str, nn.Module] = {}
    for model_name, model_info in cfg.get("models", {}).items():
        ark_cfg = model_info["arkplus_config"]
        path_key = model_info["path"]
        paths[path_key] = _build_arkplus_raw(ark_cfg)
        logger.info(
            "Built model_paths[%s] (NUM_CLASSES_LIST=%s)",
            path_key, ark_cfg.get("NUM_CLASSES_LIST"),
        )
    return paths


model_paths: dict[str, nn.Module] = _build_model_paths()
