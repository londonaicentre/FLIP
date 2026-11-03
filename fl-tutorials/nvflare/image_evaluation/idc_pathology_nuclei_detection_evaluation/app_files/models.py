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
"""The evaluated model: the detector's specification, in a form FLIP can broadcast.

``FlipEvalRecipe`` wires NVFLARE's ``GlobalModelEval`` behind ``EvaluationModelLocator``, which loads
each checkpoint named in ``config.json['models']`` **server-side** and broadcasts its weights to every
client over the ``validate`` task. That contract expects a torch ``state_dict``.

A classical detector has no learned weights -- but it does have a specification, and that
specification is exactly what has to be identical at every site for the comparison to mean anything.
So the parameters *are* the model: they are registered as buffers, travel as the broadcast payload,
and are unpacked by the evaluator. Nothing about the transport is faked, and swapping in a learned
detector later changes what fills the ``state_dict``, not how it moves.

Parameters are physical (micrometres), never pixels. The tutorial's own slides differ in
magnification -- 0.2325 um/px at one site, 0.2470 at the other -- so a pixel-valued parameter would
silently mean something different at each site and manufacture a "site effect" out of arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch import nn

# Parameter names carried in the state_dict. Order is irrelevant, but the names are the wire contract
# between the server-side checkpoint and the evaluator that unpacks it.
PARAMETER_NAMES = (
    "nucleus_diameter_um",
    "smoothing_sigma_um",
    "threshold_sigma",
    "background_disk_um",
    "min_tissue_fraction",
    "matching_radius_um",
)


class DetectorSpecification(nn.Module):
    """Holds the detector's physical parameters as float buffers so they ride the FL broadcast."""

    def __init__(self, **parameters: float) -> None:
        super().__init__()
        for name in PARAMETER_NAMES:
            self.register_buffer(name, torch.tensor(float(parameters[name]), dtype=torch.float32))

    def as_dict(self) -> dict[str, float]:
        """Return the parameters as plain floats."""
        return {name: float(getattr(self, name)) for name in PARAMETER_NAMES}


def load_config() -> dict:
    """Load the ``config.json`` sitting beside this module."""
    with open(Path(__file__).resolve().parent / "config.json") as handle:
        return json.load(handle)


def get_model() -> nn.Module:
    """Build the detector specification for this job.

    Referenced by string path (``models.get_model``) from the recipe's ``PTFileModelPersistor``, so it
    must stay zero-argument and importable from the job's ``custom/`` directory.

    Raises:
        ValueError: If ``config.json`` does not describe exactly one model, or omits a parameter.
    """
    config = load_config()
    models = config.get("models", {})
    if len(models) != 1:
        raise ValueError(f"This evaluation expects exactly one entry in config.json['models']; got {len(models)}.")
    ((name, model_config),) = models.items()

    parameters = model_config.get("parameters", {})
    missing = [key for key in PARAMETER_NAMES if key not in parameters]
    if missing:
        raise ValueError(f"Model {name!r} is missing required parameter(s): {missing}.")
    return DetectorSpecification(**{key: parameters[key] for key in PARAMETER_NAMES})
