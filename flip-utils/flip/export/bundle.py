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

Two bundle forms are written, and the choice is a real trade rather than a preference:

* ``form="torchscript"`` (the default) writes a single TorchScript file carrying ``inference.json``
  and ``metadata.json`` as TorchScript *extra files*. A MONAI Application Package built from it
  needs no FLIP application code — which is the whole reason this form exists.
* ``form="directory"`` writes a directory bundle — plain weights under ``models/``, the configs
  under ``configs/``, and the application's own code under ``scripts/``. It touches ``torch.jit``
  at no point, at the cost of shipping the application alongside the weights.

``MonaiBundleInferenceOperator`` consumes both. PyTorch has TorchScript on a removal path — it is
deprecated on Python 3.12 and reports itself unsupported on 3.14+ — so the directory form is the
escape hatch, and the one to reach for when a model will not script. ``torch.export`` is *not* an
option: no released MONAI Deploy App SDK can load an ``ExportedProgram``. See FLIP#1019.

**This function consumes the bundle configuration; it does not generate it.** The preprocessing
chain cannot be derived from a free-form training application, so the app author writes
``inference.json`` and ``metadata.json`` once, at authoring time, and they are read from disk
here. The one exception is the directory form's ``network_def`` entry, which names the copied
application's ``get_model`` — see :func:`_write_directory_bundle`. See
``docs/source/working-with-flip-apps/package-model-as-map.rst`` for how to transcribe the configs,
and why a mismatch is dangerous.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import torch
from torch import nn

from flip.export.checkpoint import load_app_model, load_weights_into_app_model
from flip.export.provenance import Provenance

logger = logging.getLogger(__name__)

ExportMethod = Literal["script", "trace"]
BundleForm = Literal["torchscript", "directory"]

#: Filenames the bundle configuration is read from, and embedded under.
INFERENCE_CONFIG_NAME = "inference.json"
METADATA_CONFIG_NAME = "metadata.json"

#: Directory-form layout. ``configs/`` and ``models/`` are fixed by ``MonaiBundleInferenceOperator``,
#: which looks for ``configs/metadata.json`` and ``models/model.ts`` falling back to
#: ``models/model.pt``. ``scripts/`` is MONAI's convention for bundle-local code; the operator puts
#: the bundle root on ``sys.path`` before instantiating the network, so a config can name a module
#: shipped inside the bundle.
CONFIGS_DIR_NAME = "configs"
MODELS_DIR_NAME = "models"
SCRIPTS_DIR_NAME = "scripts"
WEIGHTS_FILE_NAME = "model.pt"

#: Config keys the operator resolves a network from, in the order it tries them. When the author has
#: declared either, the exporter leaves the config alone rather than overriding their architecture.
_NETWORK_CONFIG_KEYS = ("network", "network_def")

#: What the injected ``network_def`` points at: ``get_model`` in the copied application's
#: ``models.py``, which is the same entry point the FL executors and :func:`load_app_model` use.
_NETWORK_TARGET = f"{SCRIPTS_DIR_NAME}.models.get_model"

#: Appended to the copied application's ``__init__.py``. FLIP application modules import one another
#: flatly (``import arkplus_flat_models``), exactly as they do under the FL executors, and
#: ``get_model`` may read a sidecar file next to its module (the spleen tutorial reads
#: ``config.json``). The bundle loader puts the *bundle root* on ``sys.path``, not this directory,
#: so importing ``scripts.models`` would otherwise fail on the first flat sibling import.
_SCRIPTS_INIT_SHIM = """
# --- appended by flip.export ---
# Put this directory on sys.path so the application's flat sibling imports resolve inside the
# bundle exactly as they do in a FLIP app. Runs before scripts.models is imported.
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(__file__))
"""

#: Copying more than this from the application directory is worth mentioning: it is source code
#: that belongs in the bundle, and anything on this scale is more likely to be a dataset a
#: researcher left beside their app than an architecture.
_SCRIPTS_SIZE_WARN_BYTES = 50_000_000

#: Never copied into ``scripts/``: build artefacts and tool caches carry no architecture.
_SCRIPTS_COPY_EXCLUDES = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".venv",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)

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
        output (Path): Where the bundle was written — a file for ``"torchscript"``, a directory for
            ``"directory"``.
        method (ExportMethod | None): Which TorchScript method succeeded, or ``None`` for the
            directory form, which compiles nothing.
        max_abs_delta (float): Largest absolute difference between the exported and eager model on
            a probe input, or ``-1.0`` when no probe input was given. Zero means the exported
            artefact is numerically identical.
        num_parameters (int): Parameter count of the exported model.
        num_state_entries (int): Number of entries loaded from the checkpoint.
        warnings (list[str]): Non-fatal concerns raised during export.
        form (BundleForm): Which bundle form was written.
    """

    output: Path
    method: ExportMethod | None
    max_abs_delta: float
    num_parameters: int
    num_state_entries: int
    warnings: list[str] = field(default_factory=list)
    form: BundleForm = "torchscript"


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


def _compile_or_explain(operation: str, compile_call: Callable[[], object]) -> object:
    """Run a ``torch.jit`` call, translating failure into advice the caller can act on.

    Two quite different things arrive here as the same exception: a model using constructs
    TorchScript cannot compile, and a PyTorch release that has removed or broken ``torch.jit``
    altogether. The remedy is the same for both — the directory form needs no ``torch.jit`` and
    compiles nothing — so they are worth naming together rather than surfacing as a bare
    ``AttributeError`` from inside the exporter.

    Args:
        operation (str): The ``torch.jit`` operation being attempted, for the message.
        compile_call (Callable[[], object]): Zero-argument call to run.

    Returns:
        object: Whatever ``compile_call`` returned.

    Raises:
        RuntimeError: If the call failed, with the directory form named as the way out.
    """
    try:
        return compile_call()
    except Exception as exc:
        raise RuntimeError(
            f"TorchScript {operation} failed: {exc}\\n"
            f"Either this model uses constructs TorchScript cannot compile, or torch.jit has been "
            f"removed — it is deprecated in PyTorch and reports itself unsupported on Python 3.14+. "
            f"Export with form='directory' instead: it writes a MONAI Bundle carrying plain weights "
            f"and the application's own code, and touches torch.jit at no point."
        ) from exc


def _write_torchscript_bundle(
    model: nn.Module,
    out: Path,
    *,
    method: ExportMethod,
    example: torch.Tensor | None,
    inference: dict,
    metadata: dict,
    warnings: list[str],
) -> float:
    """Compile the model and write it as a single TorchScript file with the configs embedded.

    Args:
        model (nn.Module): The weight-loaded model.
        out (Path): Destination file, conventionally ``model.ts``.
        method (ExportMethod): ``"script"`` or ``"trace"``.
        example (torch.Tensor | None): Probe input, when one was requested.
        inference (dict): The author's inference configuration.
        metadata (dict): The metadata configuration, provenance already merged.
        warnings (list[str]): Accumulator for non-fatal concerns.

    Returns:
        float: Largest absolute difference from the eager model, or ``-1.0`` if unverified.
    """
    if method == "script":
        exported = _compile_or_explain("scripting", lambda: torch.jit.script(model))
    else:
        exported = _compile_or_explain("tracing", lambda: torch.jit.trace(model, example, strict=False))

    max_abs_delta = -1.0
    if example is not None:
        with torch.no_grad():
            max_abs_delta = (exported(example) - model(example)).abs().max().item()  # type: ignore[operator]
    else:
        warnings.append("no example_input_shape given, so numerical equivalence was not verified")

    extra_files = {
        INFERENCE_CONFIG_NAME: json.dumps(inference, indent=2).encode(),
        METADATA_CONFIG_NAME: json.dumps(metadata, indent=2).encode(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    _compile_or_explain("save", lambda: torch.jit.save(exported, str(out), _extra_files=extra_files))
    return max_abs_delta


def _write_directory_bundle(
    model: nn.Module,
    app_dir: Path,
    out: Path,
    *,
    example: torch.Tensor | None,
    inference: dict,
    metadata: dict,
    warnings: list[str],
) -> float:
    """Write a directory-form MONAI Bundle, which needs no ``torch.jit`` to write or to read.

    The layout is the one ``MonaiBundleInferenceOperator`` looks for: ``configs/metadata.json`` and
    ``configs/inference.json``, weights at ``models/model.pt``. The operator tries ``torch.jit.load``
    on those weights, finds a plain state dict, and falls back to instantiating the network its
    config names and applying the state dict to it — which is why the application is copied into
    ``scripts/`` and ``network_def`` points at its ``get_model``.

    **This is the one piece of configuration the exporter writes rather than reads.** It is the
    architecture, which the exporter already had to resolve to load the weights at all, and it is
    injected only when the author has declared neither ``network`` nor ``network_def`` — a config
    that names its own architecture is left exactly as written.

    Args:
        model (nn.Module): The weight-loaded model.
        app_dir (Path): Application directory, copied wholesale into ``scripts/``.
        out (Path): Destination directory for the bundle.
        example (torch.Tensor | None): Probe input, when one was requested.
        inference (dict): The author's inference configuration.
        metadata (dict): The metadata configuration, provenance already merged.
        warnings (list[str]): Accumulator for non-fatal concerns.

    Returns:
        float: Largest absolute difference from the eager model, or ``-1.0`` if unverified.

    Raises:
        NotADirectoryError: If ``out`` already exists as a file.
    """
    if out.exists() and not out.is_dir():
        raise NotADirectoryError(f"form='directory' writes a bundle directory, but {out} is an existing file.")

    weights = out / MODELS_DIR_NAME / WEIGHTS_FILE_NAME
    weights.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), weights)

    # Replaced rather than merged: a module the application has since renamed would otherwise
    # linger from an earlier export and shadow the current one on the bundle's sys.path.
    scripts = out / SCRIPTS_DIR_NAME
    shutil.rmtree(scripts, ignore_errors=True)
    shutil.copytree(app_dir, scripts, ignore=shutil.ignore_patterns(*_SCRIPTS_COPY_EXCLUDES))
    init = scripts / "__init__.py"
    init.write_text((init.read_text() if init.is_file() else "") + _SCRIPTS_INIT_SHIM)

    copied_bytes = sum(item.stat().st_size for item in scripts.rglob("*") if item.is_file())
    if copied_bytes > _SCRIPTS_SIZE_WARN_BYTES:
        warnings.append(
            f"copied {copied_bytes / 1e6:.0f} MB from {app_dir.name}/ into {SCRIPTS_DIR_NAME}/ — the whole "
            f"application directory travels with the bundle, so move any data out of it before exporting"
        )

    declared = next((key for key in _NETWORK_CONFIG_KEYS if key in inference), None)
    if declared:
        warnings.append(
            f"{INFERENCE_CONFIG_NAME} already declares {declared!r}, so the bundle will build its network from "
            f"your config rather than from the copied {app_dir.name}/models.py"
        )
    else:
        inference = {**inference, "network_def": {"_target_": _NETWORK_TARGET}}

    configs = out / CONFIGS_DIR_NAME
    configs.mkdir(parents=True, exist_ok=True)
    (configs / INFERENCE_CONFIG_NAME).write_text(json.dumps(inference, indent=2))
    (configs / METADATA_CONFIG_NAME).write_text(json.dumps(metadata, indent=2))

    if example is None:
        warnings.append("no example_input_shape given, so numerical equivalence was not verified")
        return -1.0

    # Verify what was actually written, the way the operator reads it: a fresh architecture from
    # the application, with the saved state dict applied. This catches a state dict that no longer
    # fits its own models.py, which is the failure the directory form is uniquely exposed to.
    reloaded = load_app_model(app_dir)
    reloaded.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True), strict=True)
    reloaded.eval()
    with torch.no_grad():
        delta: float = (reloaded(example) - model(example)).abs().max().item()
    return delta


def export_bundle(
    checkpoint: Path,
    app_dir: Path,
    out: Path,
    *,
    inference_config: Path | None = None,
    metadata_config: Path | None = None,
    provenance: Provenance | None = None,
    form: BundleForm = "torchscript",
    method: ExportMethod = "script",
    example_input_shape: tuple[int, ...] | None = None,
    job_type: str = "",
    allow_pickle: bool = False,
    exported_at: datetime | None = None,
) -> ExportResult:
    """Export a FLIP checkpoint as a MONAI Bundle.

    Args:
        checkpoint (Path): The aggregated checkpoint, e.g. ``FL_global_model.pt``.
        app_dir (Path): Application directory holding ``models.py``.
        out (Path): Destination. A file for ``form="torchscript"``, conventionally ``model.ts``; a
            directory for ``form="directory"``.
        inference_config (Path | None): Path to ``inference.json``. Defaults to
            ``<app_dir>/../export/inference.json``.
        metadata_config (Path | None): Path to ``metadata.json``. Defaults to
            ``<app_dir>/../export/metadata.json``.
        provenance (Provenance | None): Federated-run record to embed. When omitted, a minimal
            record is still written so the artefact is never untraceable.
        form (BundleForm): ``"torchscript"`` (default) for a single self-contained file that needs
            no FLIP application code, or ``"directory"`` for a bundle directory that needs no
            ``torch.jit``. See the module docstring for the trade-off.
        method (ExportMethod): ``"script"`` (default) or ``"trace"``. Scripting needs no example
            input, which matters because packaging runs where no imaging data exists. Ignored by
            ``form="directory"``, which compiles nothing.
        example_input_shape (tuple[int, ...] | None): Probe input shape. Required for ``"trace"``;
            optional otherwise, where it enables the numerical equivalence check.
        job_type (str): Originating job type, used only to warn when a checkpoint is unlikely to
            be worth packaging.
        allow_pickle (bool): Load the checkpoint with ``weights_only=False``. Only for checkpoints
            of known provenance.
        exported_at (datetime | None): Export timestamp. Defaults to now, in UTC.

    Returns:
        ExportResult: Where the bundle was written and how it verified.

    Raises:
        FileNotFoundError: If the checkpoint, ``models.py`` or a config is missing.
        NotADirectoryError: If ``form="directory"`` and ``out`` is an existing file.
        RuntimeError: If the weights do not load into the declared architecture, or TorchScript
            compilation fails.
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

    if form == "torchscript" and method == "trace" and example_input_shape is None:
        raise ValueError("method='trace' requires example_input_shape; tracing needs a concrete input.")

    example = torch.randn(*example_input_shape) if example_input_shape else None

    resolved = provenance or Provenance()
    if not resolved.source_checkpoint:
        resolved.source_checkpoint = checkpoint.name
    stamped_metadata = resolved.merged_into(metadata, exported_at)

    if form == "directory":
        max_abs_delta = _write_directory_bundle(
            model, app_dir, out, example=example, inference=inference, metadata=stamped_metadata, warnings=warnings
        )
    else:
        max_abs_delta = _write_torchscript_bundle(
            model,
            out,
            method=method,
            example=example,
            inference=inference,
            metadata=stamped_metadata,
            warnings=warnings,
        )

    if max_abs_delta > 0:
        warnings.append(f"exported model differs from eager by {max_abs_delta:.3e} on the probe input")

    logger.info("Exported %s bundle to %s (%d parameters)", form, out, num_parameters)
    for warning in warnings:
        logger.warning("%s", warning)

    return ExportResult(
        output=out,
        method=None if form == "directory" else method,
        max_abs_delta=max_abs_delta,
        num_parameters=num_parameters,
        num_state_entries=num_state_entries,
        warnings=warnings,
        form=form,
    )
