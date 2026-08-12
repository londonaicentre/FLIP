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

"""Reading FLIP training checkpoints and the architectures they belong to.

FLIP produces two on-disk checkpoint shapes, and both are handled here:

* **NVFLARE persistence format** — a dict with a ``model`` key holding the state dict, alongside
  optional ``train_conf`` and ``meta_props``. This is what ``PTFileModelPersistor`` writes, so it
  is the shape of the aggregated ``FL_global_model.pt`` and the per-client ``local_model.pt``.
* **Bare state dict** — parameter names at the top level, as carried by user-uploaded evaluation
  checkpoints.

``PTModelPersistenceFormatManager`` normalises both. It is used here rather than re-implementing
the discrimination, so that reading a checkpoint stays faithful to how FLIP itself reads one.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch
from nvflare.app_opt.pt.model_persistence_format_manager import PTModelPersistenceFormatManager
from torch import nn

# Buffers PyTorch defines as integer-valued. FLIP's aggregation averages every entry of the state
# dict as a float, so these return from the server as floating point even though the module
# declares them integral. ``load_state_dict`` casts them back on copy, so strict loading is
# unaffected — but anything reading the raw dict must not assume dtypes survive aggregation.
_INTEGER_BUFFER_SUFFIX = "num_batches_tracked"


@dataclass
class CheckpointFacts:
    """Structural facts about a checkpoint, as read from disk.

    Attributes:
        path (Path): Where the checkpoint was read from.
        is_persistence_format (bool): True when the NVFLARE persistence format was detected.
        num_entries (int): Number of entries in the state dict.
        num_elements (int): Total number of tensor elements.
        dtype_histogram (dict[str, int]): Count of entries per dtype.
        prefixes (list[str]): Distinct first-level key prefixes.
        dataparallel_keys (list[str]): Keys carrying a ``module.`` prefix.
        promoted_buffers (list[str]): Integer buffers arriving as floating point.
        train_conf (dict | None): The checkpoint's ``train_conf`` block, when present.
        meta_props (dict | None): The checkpoint's ``meta_props`` block, when present.
    """

    path: Path
    is_persistence_format: bool
    num_entries: int
    num_elements: int
    dtype_histogram: dict[str, int] = field(default_factory=dict)
    prefixes: list[str] = field(default_factory=list)
    dataparallel_keys: list[str] = field(default_factory=list)
    promoted_buffers: list[str] = field(default_factory=list)
    train_conf: dict | None = None
    meta_props: dict | None = None


def load_checkpoint(path: Path, allow_pickle: bool = False) -> dict[str, Any]:
    """Load a checkpoint from disk.

    Args:
        path (Path): Path to the ``.pt`` file.
        allow_pickle (bool): Load with ``weights_only=False``, permitting arbitrary object
            deserialisation. Only enable this for a checkpoint of known provenance — it allows
            execution of arbitrary code embedded in the file.

    Returns:
        dict[str, Any]: The deserialised checkpoint.

    Raises:
        FileNotFoundError: If the checkpoint does not exist.
        TypeError: If the checkpoint does not deserialise to a dictionary.
    """
    if not path.is_file():
        raise FileNotFoundError(f"No checkpoint at {path}")

    data = torch.load(path, map_location="cpu", weights_only=not allow_pickle)
    if not isinstance(data, dict):
        raise TypeError(f"Expected a dict-like checkpoint, got {type(data).__name__}")
    return data


def state_dict_from(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the state dict from either supported checkpoint shape.

    Args:
        data (dict[str, Any]): A deserialised checkpoint.

    Returns:
        dict[str, Any]: The parameter dictionary.
    """
    var_dict: dict[str, Any] = PTModelPersistenceFormatManager(data, default_train_conf=None).var_dict
    return var_dict


def describe_checkpoint(data: dict[str, Any], path: Path) -> CheckpointFacts:
    """Summarise a checkpoint's structure without loading it into a model.

    Args:
        data (dict[str, Any]): A deserialised checkpoint.
        path (Path): Path the checkpoint was read from, for reporting only.

    Returns:
        CheckpointFacts: The structural facts.
    """
    manager = PTModelPersistenceFormatManager(data, default_train_conf=None)
    state_dict = manager.var_dict
    tensors = {key: value for key, value in state_dict.items() if isinstance(value, torch.Tensor)}

    return CheckpointFacts(
        path=path,
        is_persistence_format=PTModelPersistenceFormatManager.PERSISTENCE_KEY_MODEL in data,
        num_entries=len(state_dict),
        num_elements=sum(tensor.numel() for tensor in tensors.values()),
        dtype_histogram=dict(Counter(str(tensor.dtype) for tensor in tensors.values())),
        prefixes=sorted({key.split(".")[0] for key in state_dict}),
        dataparallel_keys=[key for key in state_dict if key.startswith("module.")],
        promoted_buffers=[
            key
            for key, tensor in tensors.items()
            if key.endswith(_INTEGER_BUFFER_SUFFIX) and tensor.dtype.is_floating_point
        ],
        train_conf=manager.train_conf,
        meta_props=manager.meta,
    )


def load_app_model(app_dir: Path) -> nn.Module:
    """Instantiate an application's model via its ``models.py`` ``get_model()``.

    Args:
        app_dir (Path): Directory holding the application's ``models.py``, per the FLIP
            application contract.

    Returns:
        nn.Module: The instantiated architecture.

    Raises:
        FileNotFoundError: If ``models.py`` is not present in ``app_dir``.
        ImportError: If ``models.py`` cannot be loaded.
        AttributeError: If ``models.py`` does not export ``get_model``.
        TypeError: If ``get_model()`` does not return a ``torch.nn.Module``.
    """
    models_path = app_dir / "models.py"
    if not models_path.is_file():
        raise FileNotFoundError(f"No models.py in {app_dir} — the FLIP app contract requires one.")

    # The app directory goes on sys.path because application modules import each other flatly
    # (``from models import get_model``), exactly as the NVFLARE executors load them at runtime.
    sys.path.insert(0, str(app_dir))
    spec = importlib.util.spec_from_file_location("models", models_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {models_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["models"] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "get_model"):
        raise AttributeError(f"{models_path} does not export get_model() — required by the FLIP app contract.")

    model = module.get_model()
    if not isinstance(model, nn.Module):
        raise TypeError(f"get_model() in {models_path} returned {type(model).__name__}, expected a torch.nn.Module.")
    return model


def load_weights_into_app_model(checkpoint: Path, app_dir: Path, allow_pickle: bool = False) -> nn.Module:
    """Load a checkpoint's weights into the architecture its application declares.

    This is the assumption the whole packaging path rests on: that the aggregated weights and the
    architecture still fit one another once training has finished. The load is strict, so a
    mismatch raises rather than silently producing a partially-initialised model.

    Args:
        checkpoint (Path): Path to the checkpoint.
        app_dir (Path): Directory holding the application's ``models.py``.
        allow_pickle (bool): Passed through to :func:`load_checkpoint`.

    Returns:
        nn.Module: The model in eval mode with the checkpoint's weights loaded.

    Raises:
        RuntimeError: If the weights do not match the architecture.
    """
    model = load_app_model(app_dir)
    model.load_state_dict(state_dict_from(load_checkpoint(checkpoint, allow_pickle=allow_pickle)), strict=True)
    model.eval()
    return model
