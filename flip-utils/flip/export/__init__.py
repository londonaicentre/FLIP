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
#

"""Packaging a FLIP-trained model for inference.

Turns a completed training run's checkpoint into a MONAI Bundle, which can then be packaged as a
MONAI Application Package (MAP) — a container that takes DICOM in and emits DICOM out, deployable
into a radiology workflow.

Packaging is a **separate step from training**: it runs against an already-uploaded checkpoint,
never inside the federated job, so a packaging failure cannot affect a training run and the FL
runtime carries no deployment dependencies.

Example:

.. code-block:: python

    from pathlib import Path

    from flip.export import Provenance, export_bundle

    result = export_bundle(
        checkpoint=Path("FL_global_model.pt"),
        app_dir=Path("app_files"),
        out=Path("bundle/model.ts"),
        provenance=Provenance(model_id="…", project_id="…", participating_trusts=["GSTT", "KCH"]),
        example_input_shape=(1, 1, 96, 96, 96),
    )
    print(result.method, result.max_abs_delta)

This subpackage requires PyTorch, which ships in the ``full`` extra rather than the base install:

.. code-block:: bash

    pip install "flip-utils[full]"

See ``docs/source/working-with-flip-apps/package-model-as-map.rst`` for the end-to-end guide.
"""

try:  # pragma: no cover - exercised by environment, not by tests
    import torch  # noqa: F401
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "flip.export requires PyTorch, which is not part of the base flip-utils install. "
        'Install the optional dependencies with: pip install "flip-utils[full]"'
    ) from exc

from flip.export.bundle import (
    EXPORTABLE_JOB_TYPES,
    ExportMethod,
    ExportResult,
    export_bundle,
)
from flip.export.checkpoint import (
    CheckpointFacts,
    describe_checkpoint,
    load_app_model,
    load_checkpoint,
    load_weights_into_app_model,
    state_dict_from,
)
from flip.export.provenance import PROVENANCE_KEY, Provenance

__all__ = [
    "EXPORTABLE_JOB_TYPES",
    "PROVENANCE_KEY",
    "CheckpointFacts",
    "ExportMethod",
    "ExportResult",
    "Provenance",
    "describe_checkpoint",
    "export_bundle",
    "load_app_model",
    "load_checkpoint",
    "load_weights_into_app_model",
    "state_dict_from",
]
