"""Checkpoint loading utilities for Ark+ preprocessing.

These functions handle the raw checkpoint format differences (``module.``
prefix, timm downsample remapping, computed buffers) so that the cleaned
output passes ``strict=True`` on the server side.

Import this from ``preprocess_checkpoints.py``, not from the evaluator app.
"""

from __future__ import annotations

import logging
from pathlib import Path

import torch
from torch import nn

logger = logging.getLogger(__name__)


def load_arkplus_state_dict(
    model: nn.Module, raw_state_dict: dict, arkplus_config: dict
) -> nn.Module:
    """Load *raw_state_dict* (from checkpoint) into *model*.

    Handles ``module.`` and ``ark_model.`` prefix stripping,
    ``layers.X.downsample.`` remapping (timm version differences),
    and ``attn_mask`` / ``omni_heads.*`` filtering.
    """
    load_backbone_only = bool(arkplus_config.get("LOAD_BACKBONE_ONLY", False))

    state_dict = _strip_key_prefix(raw_state_dict)
    state_dict = _remap_downsample_keys(state_dict, model)
    state_dict = _filter_state_dict(model, state_dict, load_backbone_only)

    msg = model.load_state_dict(state_dict, strict=False)
    n_missing = len(msg.missing_keys)
    n_unexpected = len(msg.unexpected_keys)
    if n_missing + n_unexpected:
        logger.info(
            "Loaded checkpoint: missing=%d unexpected=%d",
            n_missing, n_unexpected,
        )
    else:
        logger.info("Loaded checkpoint successfully (exact match).")
    return model


def _strip_key_prefix(state_dict: dict) -> dict:
    """Remove ``module.`` or ``ark_model.`` key prefix if present."""
    out = {}
    for k, v in state_dict.items():
        if k.startswith("module."):
            out[k[7:]] = v
        elif k.startswith("ark_model."):
            out[k[10:]] = v
        else:
            out[k] = v
    return out


def _remap_downsample_keys(state_dict: dict, model: nn.Module) -> dict:
    """Remap Swin downsample keys for installed timm layout."""
    model_state = model.state_dict()
    remapped = {}
    changed = 0
    mappings = (
        ("layers.0.downsample.", "layers.1.downsample."),
        ("layers.1.downsample.", "layers.2.downsample."),
        ("layers.2.downsample.", "layers.3.downsample."),
    )
    for key, value in state_dict.items():
        new_key = key
        for src, dst in mappings:
            if key.startswith(src):
                candidate = dst + key[len(src):]
                if candidate in model_state and tuple(value.shape) == tuple(
                    model_state[candidate].shape
                ):
                    new_key = candidate
                    changed += 1
                break
        remapped[new_key] = value
    if changed:
        logger.info("Remapped %d Swin downsample tensors for installed timm.", changed)
    return remapped


def _filter_state_dict(
    model: nn.Module, state_dict: dict, load_backbone_only: bool
) -> dict:
    """Keep only compatible tensors (matching shape, skip attn_mask / heads)."""
    model_state = model.state_dict()
    compatible = {}
    skipped = []
    for key, value in state_dict.items():
        if "attn_mask" in key:
            skipped.append(key)
            continue
        if load_backbone_only and key.startswith("omni_heads."):
            skipped.append(key)
            continue
        if key not in model_state:
            skipped.append(key)
            continue
        if tuple(value.shape) != tuple(model_state[key].shape):
            skipped.append(key)
            continue
        compatible[key] = value
    if skipped:
        logger.info(
            "Skipped %d incompatible tensors (sample: %s).",
            len(skipped), skipped[:5],
        )
    return compatible
