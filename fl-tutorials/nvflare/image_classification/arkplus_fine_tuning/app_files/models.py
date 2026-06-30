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

import arkplus_flat_models
import torch
from torch import nn

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
