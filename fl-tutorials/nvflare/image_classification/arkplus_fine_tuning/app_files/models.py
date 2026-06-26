"""NVFLARE entry model for Ark+ inside the FLIP x-ray tutorial.

NVFLARE's PTFileModelPersistor loads ``models.get_model`` from this file.
This adapter constructs the Ark+ FedArk chest-xray model from flat files in this
directory so it can be uploaded through FLIP interfaces that do not support
nested folders.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import torch
from torch import nn

import arkplus_flat_models


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)



class ArkPlusNVFlareWrapper(nn.Module):
    """Wrap Ark+'s multi-head output into the simple classifier shape FLIP expects.

    The real Ark+ model returns ``(features, logits)`` when called with a head id.
    The existing FLIP x-ray trainer/validator expects ``model(images) -> logits``.
    This wrapper keeps the real Ark+ model as ``self.ark_model`` while exposing a
    normal classifier forward for the NVFLARE tutorial.
    """

    def __init__(self, ark_model: nn.Module, head_id: int = 0, num_classes: int = 2):
        super().__init__()
        self.ark_model = ark_model
        self.head_id = int(head_id)
        self.num_classes = int(num_classes)

    def _pool_logits(self, logits: torch.Tensor) -> torch.Tensor:
        """Convert Ark+ spatial logits to the [batch, classes] shape FLIP expects."""
        if logits.ndim == 4:
            # Ark+ may return [B, H, W, C] or [B, C, H, W].
            num_classes = int(self.num_classes)
            if logits.shape[-1] == num_classes:
                logits = logits.mean(dim=(1, 2))
            elif logits.shape[1] == num_classes:
                logits = logits.mean(dim=(2, 3))
            else:
                logits = logits.flatten(start_dim=1)
        return logits

    def state_dict(self, *args, **kwargs):
        return self.ark_model.state_dict(*args, **kwargs)

    def load_state_dict(self, state_dict, strict=True):
        keys = list(state_dict.keys())
        if keys and keys[0].startswith("ark_model."):
            return super().load_state_dict(state_dict, strict=strict)
        wrapped = {f"ark_model.{k}": v for k, v in state_dict.items()}
        return super().load_state_dict(wrapped, strict=strict)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _features, logits = self.ark_model(x, self.head_id)
        return self._pool_logits(logits)

    def forward_with_features(self, x: torch.Tensor):
        features, logits = self.ark_model(x, self.head_id)
        return features, self._pool_logits(logits)


def get_model() -> nn.Module:
    """Return a fresh Ark+ model for NVFLARE.

    This zero-argument function is required by the FLIP/NVFLARE standard job
    config: ``models.get_model``.
    """
    cfg = _load_config()
    ark_cfg = cfg.get("ARKPLUS", {})
    num_classes_list = ark_cfg.get("NUM_CLASSES_LIST", [2])
    head_id = int(ark_cfg.get("HEAD_ID", 0))

    PRETRAINED_PATH = APP_DIR / "pretrained_weights.pt"
    if not PRETRAINED_PATH.is_file():
        raise FileNotFoundError(
            f"Pretrained weights not found at {PRETRAINED_PATH}. "
            "This file must exist — it supplies the backbone initialization. "
            "Run: cp tutorials/image_evaluation/xray_evaluation/app_files/"
            "arkplus_pretrained_weights.pt tutorials/image_classification/"
            "xray_classification/app_files/pretrained_weights"
        )

    try:
        args = SimpleNamespace(
            model_name=ark_cfg.get("MODEL_NAME", "swin_base"),
            input_size=int(ark_cfg.get("INPUT_SIZE", 224)),
            projector_features=ark_cfg.get("PROJECTOR_FEATURES", 1376),
            use_mlp=bool(ark_cfg.get("USE_MLP", False)),
            pretrained_weights=str(PRETRAINED_PATH),
            pretrained_key=None,
            load_backbone_only=bool(ark_cfg.get("LOAD_BACKBONE_ONLY", False)),
        )
        ark_model = arkplus_flat_models.build_omni_model(args, num_classes_list=num_classes_list)
        return ArkPlusNVFlareWrapper(
            ark_model=ark_model,
            head_id=head_id,
            num_classes=num_classes_list[head_id],
        )
    except Exception as exc:
        raise RuntimeError(
            "Failed to build the real Ark+ FedArk model. This commonly means the "
            "runtime is missing Ark+ dependencies such as `timm`. Add them to the "
            "root flip-fl-base pyproject/uv environment or set "
            "ARKPLUS.REQUIRE_ARKPLUS_IMPORT=false only for a non-Ark smoke test. "
            f"Original error: {exc!r}"
        ) from exc
        