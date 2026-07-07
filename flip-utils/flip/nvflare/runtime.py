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

"""Runtime resolution of FLIP-specific job parameters from NVFLARE's job metadata.

The FLIP-API hands job-scoped values (``model_id``, optionally ``participating_clients``)
to NVFLARE via ``meta.json``'s ``custom_props`` dict. FLIP components fetch these at first
use via :func:`get_flip_model_id` rather than requiring them as ``__init__`` arguments —
this lets the Python ``FlipFedAvgRecipe`` build a real ``FedJob`` (no UUID known yet) that
runs identically in SimEnv, PocEnv, ProdEnv, or under the FLIP-API.

Resolution order, given a component that also accepts a legacy ``model_id`` constructor arg:

1. If the constructor arg is a real UUID (e.g. the FLIP-API substituted ``{model_id}`` into
   the JSON config before NVFLARE loaded it), use it.
2. Otherwise read ``fl_ctx.get_prop(FLContextKey.JOB_META)["custom_props"]["model_id"]``.
3. Otherwise raise ``ValueError`` — neither path produced a UUID.
"""

from __future__ import annotations

import json
from typing import Any

from nvflare.apis.fl_constant import FLContextKey
from nvflare.apis.fl_context import FLContext

from flip.utils.utils import Utils

FLIP_CUSTOM_PROPS_KEY = "custom_props"
FLIP_MODEL_ID_KEY = "model_id"
FLIP_PARTICIPATING_CLIENTS_KEY = "participating_clients"


def get_job_custom_props(fl_ctx: FLContext) -> dict[str, Any]:
    """Return the ``custom_props`` dict from the job's ``meta.json``, or ``{}`` if absent.

    NVFLARE sets ``FLContextKey.JOB_META`` with ``sticky=False``, so it's only on the
    fl_ctx during the component-build phase — gone by the time controllers fire events.
    Fall back to reading ``meta.json`` straight from the workspace's run dir.
    """
    meta = fl_ctx.get_prop(FLContextKey.JOB_META, None)
    if not isinstance(meta, dict):
        meta = _load_meta_from_workspace(fl_ctx)
    if not isinstance(meta, dict):
        return {}
    props = meta.get(FLIP_CUSTOM_PROPS_KEY, {})
    return props if isinstance(props, dict) else {}


def _load_meta_from_workspace(fl_ctx: FLContext) -> dict[str, Any] | None:
    """Read ``meta.json`` from the run dir if the FLContext doesn't carry it."""
    engine = fl_ctx.get_engine()
    if engine is None:
        return None
    try:
        workspace = engine.get_workspace()
        job_id = fl_ctx.get_job_id()
        if not workspace or not job_id:
            return None
        meta_path = workspace.get_job_meta_path(job_id)
        with open(meta_path) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_flip_model_id(fl_ctx: FLContext, fallback: str | None = None) -> str:
    """Resolve the FLIP ``model_id`` for the current job.

    Args:
        fl_ctx: The NVFLARE FLContext.
        fallback: Value passed to the component's ``__init__`` (legacy path). If this is a
            valid UUID it is returned as-is; otherwise it's ignored.

    Returns:
        A UUID string.

    Raises:
        ValueError: If neither ``fallback`` nor ``meta.json``'s ``custom_props.model_id``
            holds a valid UUID.
    """
    if fallback and Utils.is_valid_uuid(fallback):
        return fallback

    props = get_job_custom_props(fl_ctx)
    candidate = props.get(FLIP_MODEL_ID_KEY)
    if candidate and Utils.is_valid_uuid(candidate):
        return str(candidate)

    raise ValueError(
        "FLIP model_id is not available: pass a valid UUID to the component's model_id arg, "
        f"or set meta.json['{FLIP_CUSTOM_PROPS_KEY}']['{FLIP_MODEL_ID_KEY}'] before submitting the job."
    )
