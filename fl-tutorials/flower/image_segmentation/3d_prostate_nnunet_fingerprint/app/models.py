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

"""The "model" of the fingerprint job: a parameter-free module whose buffers are the histograms.

FLIP's job types move a torch ``state_dict`` between server and sites and average it. Nothing is
trained here, so the model is a module holding the arrays ``app.fingerprint`` describes, sized from
``config.json`` next to this file. The downloaded model is the pooled fingerprint.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch import nn

from app.fingerprint import STATE_KEYS, state_shapes


def load_config() -> dict:
    with open(Path(__file__).with_name("config.json")) as handle:
        return json.load(handle)


class FingerprintState(nn.Module):
    """Zero-initialised float32 buffers, one per exchanged array."""

    def __init__(self) -> None:
        super().__init__()
        shapes = state_shapes(load_config())
        for key in STATE_KEYS:
            self.register_buffer(key, torch.zeros(shapes[key], dtype=torch.float32))


def get_model() -> nn.Module:
    """The state-holding module (zero-argument, as the template server requires)."""
    return FingerprintState()
