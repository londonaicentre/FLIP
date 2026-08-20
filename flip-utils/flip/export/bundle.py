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

"""Turning a FLIP training checkpoint into a MONAI Bundle.

The exported artefact is a single TorchScript file carrying ``inference.json`` and
``metadata.json`` as TorchScript *extra files*. That is the shape
``MonaiBundleInferenceOperator`` consumes, and it is what removes the dependency on FLIP
application code — a MONAI Application Package built from it needs no ``models.py``.

**This function consumes the bundle configuration; it does not generate it.** The preprocessing
chain cannot be derived from a free-form training application, so the app author writes
``inference.json`` and ``metadata.json`` once, at authoring time, and they are read from disk
here. See ``docs/source/working-with-flip-apps/package-model-as-map.rst`` for how to transcribe
them, and why a mismatch is dangerous.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import torch

from flip.export.checkpoint import load_weights_into_app_model
from flip.export.provenance import Provenance

logger = logging.getLogger(__name__)

ExportMethod = Literal["script", "trace"]

#: Filenames the bundle configuration is read from, and embedded under.
INFERENCE_CONFIG_NAME = "inference.json"
METADATA_CONFIG_NAME = "metadata.json"

#: Job types that produce a new model and are therefore worth exporting. A job type outside this
#: set is warned about rather than rejected: this runs as a deliberate, manual step, so refusing
#: to package a checkpoint the caller has explicitly pointed at would be unhelpful.
# "standard_client_api" is the pre-rename alias of the standard job type, kept for models
# created before the Client-API templates took over the plain names.
EXPORTABLE_JOB_TYPES = frozenset({"standard", "standard_client_api", "fed_opt"})


@dataclass
class ExportResult:
    """Outcome of an export.

    Attributes:
        output (Path): Where the bundle was written.
        method (ExportMethod): Which TorchScript method succeeded.
        max_abs_delta (float): Largest absolute difference between the scripted and eager model on
            a probe input. Zero means the exported artefact is numerically identical.
        num_parameters (int): Parameter count of the exported model.
        num_state_entries (int): Number of entries loaded from the checkpoint.
        warnings (list[str]): Non-fatal concerns raised during export.
    """

    output: Path
    method: ExportMethod
    max_abs_delta: float
    num_parameters: int
    num_state_entries: int
    warnings: list[str] = field(default_factory=list)


def _read_config(explicit: Path | None, app_dir: Path, name: str) -> dict:
    """Read a bundle config, defaulting to the ``export/`` directory beside the application.

    Args:
        explicit (Path | None): Caller-supplied path, if any.
        app_dir (Path): The application directory.
        name (str): Config filename to look for.

    Returns:
        dict: The parsed configuration.

    Raises:
        FileNotFoundError: If the config cannot be found.
    """
    path = explicit or (app_dir.parent / "export" / name)
    if not path.is_file():
        raise FileNotFoundError(
            f"No {name} at {path}. The bundle configuration is written by the app author, not "
            f"generated — see the packaging guide for how to transcribe it."
        )
    config: dict = json.loads(path.read_text())
    return config


def export_bundle(
    checkpoint: Path,
    app_dir: Path,
    out: Path,
    *,
    inference_config: Path | None = None,
    metadata_config: Path | None = None,
    provenance: Provenance | None = None,
    method: ExportMethod = "script",
    example_input_shape: tuple[int, ...] | None = None,
    job_type: str = "",
    allow_pickle: bool = False,
    exported_at: datetime | None = None,
) -> ExportResult:
    """Export a FLIP checkpoint as a MONAI Bundle in TorchScript form.

    Args:
        checkpoint (Path): The aggregated checkpoint, e.g. ``FL_global_model.pt``.
        app_dir (Path): Application directory holding ``models.py``.
        out (Path): Destination for the bundle, conventionally ``model.ts``.
        inference_config (Path | None): Path to ``inference.json``. Defaults to
            ``<app_dir>/../export/inference.json``.
        metadata_config (Path | None): Path to ``metadata.json``. Defaults to
            ``<app_dir>/../export/metadata.json``.
        provenance (Provenance | None): Federated-run record to embed. When omitted, a minimal
            record is still written so the artefact is never untraceable.
        method (ExportMethod): ``"script"`` (default) or ``"trace"``. Scripting needs no example
            input, which matters because packaging runs where no imaging data exists.
        example_input_shape (tuple[int, ...] | None): Probe input shape. Required for ``"trace"``;
            optional for ``"script"``, where it enables the numerical equivalence check.
        job_type (str): Originating job type, used only to warn when a checkpoint is unlikely to
            be worth packaging.
        allow_pickle (bool): Load the checkpoint with ``weights_only=False``. Only for checkpoints
            of known provenance.
        exported_at (datetime | None): Export timestamp. Defaults to now, in UTC.

    Returns:
        ExportResult: Where the bundle was written and how it verified.

    Raises:
        FileNotFoundError: If the checkpoint, ``models.py`` or a config is missing.
        RuntimeError: If the weights do not load into the declared architecture.
        ValueError: If ``method="trace"`` without an ``example_input_shape``.
    """
    warnings: list[str] = []
    if job_type and job_type not in EXPORTABLE_JOB_TYPES:
        warnings.append(
            f"job_type {job_type!r} is not one of {sorted(EXPORTABLE_JOB_TYPES)}; an evaluation job "
            f"produces no new model, so this checkpoint may not be what you intend to deploy."
        )

    inference = _read_config(inference_config, app_dir, INFERENCE_CONFIG_NAME)
    metadata = _read_config(metadata_config, app_dir, METADATA_CONFIG_NAME)

    model = load_weights_into_app_model(checkpoint, app_dir, allow_pickle=allow_pickle)
    num_state_entries = len(model.state_dict())
    num_parameters = sum(parameter.numel() for parameter in model.parameters())

    if method == "trace" and example_input_shape is None:
        raise ValueError("method='trace' requires example_input_shape; tracing needs a concrete input.")

    example = torch.randn(*example_input_shape) if example_input_shape else None

    if method == "script":
        exported = torch.jit.script(model)
    else:
        exported = torch.jit.trace(model, example, strict=False)

    max_abs_delta = -1.0
    if example is not None:
        with torch.no_grad():
            max_abs_delta = (exported(example) - model(example)).abs().max().item()
        if max_abs_delta > 0:
            warnings.append(f"exported model differs from eager by {max_abs_delta:.3e} on the probe input")
    else:
        warnings.append("no example_input_shape given, so numerical equivalence was not verified")

    resolved = provenance or Provenance()
    if not resolved.source_checkpoint:
        resolved.source_checkpoint = checkpoint.name

    extra_files = {
        INFERENCE_CONFIG_NAME: json.dumps(inference, indent=2).encode(),
        METADATA_CONFIG_NAME: json.dumps(resolved.merged_into(metadata, exported_at), indent=2).encode(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.jit.save(exported, str(out), _extra_files=extra_files)

    logger.info("Exported bundle to %s (%s, %d parameters)", out, method, num_parameters)
    for warning in warnings:
        logger.warning("%s", warning)

    return ExportResult(
        output=out,
        method=method,
        max_abs_delta=max_abs_delta,
        num_parameters=num_parameters,
        num_state_entries=num_state_entries,
        warnings=warnings,
    )
