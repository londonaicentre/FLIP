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

"""The "model" of the causal-discovery tutorial: a parameter-free module holding the federated state.

FLIP's job types move a torch ``state_dict`` between server and sites and average it. There is
no neural network here, so the model is a module whose *buffers* are the arrays
``federated_fci`` exchanges — site counts, pooled statistics, the voted skeleton and separating
sets, and the bootstrap PAGs. Shapes come from ``config.json`` next to this file (variable
count and bootstrap count), and the factory takes no arguments because both backends call
``get_model()`` bare (NVFLARE's persistor references ``models.get_model`` directly).

Kept byte-identical between the NVFLARE and Flower copies — drift-checked by
``scripts/check_tutorial_sync.sh``.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch import nn

try:  # NVFLARE imports app_files/ flat; the Flower bundle imports it as the ``app`` package.
    from federated_fci import STATE_KEYS, state_shapes
except ImportError:  # pragma: no cover - exercised on the Flower side
    from app.federated_fci import STATE_KEYS, state_shapes


def _load_config() -> dict:
    with open(Path(__file__).resolve().parent / "config.json") as fh:
        return json.load(fh)


class CausalDiscoveryState(nn.Module):
    """Zero-initialised float32 buffers, one per wire-format array, in ``STATE_KEYS`` order."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        shapes = state_shapes(config if config is not None else _load_config())
        for key in STATE_KEYS:
            self.register_buffer(key, torch.zeros(shapes[key], dtype=torch.float32))

    def forward(self, *_args, **_kwargs):  # pragma: no cover - never called; the module only carries state
        raise NotImplementedError("CausalDiscoveryState holds federated arrays; it does not compute anything")


def get_model() -> nn.Module:
    """The state-holding module, sized from ``config.json`` (zero-arg, as both backends require)."""
    return CausalDiscoveryState()
