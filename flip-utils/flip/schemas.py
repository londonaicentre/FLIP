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

"""Request schemas for the payloads the flip package POSTs to the Central Hub.

These mirror the Pydantic models the hub validates against (flip-api
``domain/schemas/private.py``). They are separate codebases, so the two
definitions must be kept in sync — the snake_case field names below are the
on-the-wire contract for the ``/model/{id}/metrics`` and ``/model/{id}/logs``
internal endpoints.
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

# Default label naming the x-axis of an FL training-metric plot, applied when a metric is sent without
# an explicit x-axis label (the historical behaviour: the x-axis is the FL global round). Mirrors
# flip-api's flip_api.utils.constants.DEFAULT_X_AXIS_LABEL — keep the two in sync (FLIP#148).
DEFAULT_X_AXIS_LABEL = "Global Round"


def split_x_label(key: str) -> tuple[str, str | None]:
    """Split a metric key of the form ``<label>[@<x_label>]`` into ``(label, x_label)``.

    The ``@<x_label>`` segment names the x-axis a metric is plotted against (FLIP#148); absent, the
    x_label is ``None`` and the hub defaults it to "Global Round". Shared by every path that encodes
    the x-label inside a metric name (Flower MetricRecord keys, NVFLARE Client-API SummaryWriter tags).

    Args:
        key (str): The metric key, e.g. ``"train_loss@epoch"`` or ``"train_loss"``.

    Returns:
        tuple[str, str | None]: The bare label and the x-label (``None`` when the key has none).
    """
    if "@" in key:
        label, x_label = key.split("@", 1)
        return label, x_label
    return key, None


class TrainingMetrics(BaseModel):
    """A single training/evaluation metric value reported for one FL client.

    ``fl_client_name`` is the FL client's identity as the FL server sees it —
    the FL participant name for NVFLARE, the SUPERNODE_NAME for Flower. The hub resolves
    it to a trust before storing the metric.

    ``global_round`` is provenance — always the FL global round the metric was reported in, never
    overridden. The plot coordinate is the (``x_label``, ``x_value``) pair, defaulting to the global
    round on the "Global Round" axis — see FLIP#148.
    """

    fl_client_name: str
    global_round: int = Field(ge=0)
    label: str
    result: float
    # The x-coordinate this metric is plotted at. Defaults to the global round (see the validator
    # below). nan/inf are rejected: they would survive to the DB and then break JSON-encoding the
    # hub's metrics response for the whole model. (`result` has the same pre-existing hazard.)
    x_value: float = Field(allow_inf_nan=False)
    # Label naming the x-axis this metric is plotted against; defaults to the FL global round axis when
    # the client doesn't send one. A plot's identity is the (label, x_label) pair — see FLIP#148.
    x_label: str = Field(default=DEFAULT_X_AXIS_LABEL)

    @model_validator(mode="before")
    @classmethod
    def _default_x_value_to_global_round(cls, data: Any) -> Any:
        """Backfill a missing/None ``x_value`` from ``global_round`` (back-compat with old senders)."""
        if isinstance(data, dict) and data.get("x_value") is None:
            global_round = data.get("global_round")
            if global_round is not None:  # absent global_round -> let the field-required error surface
                data = {**data, "x_value": global_round}
        return data


class TrainingLog(BaseModel):
    """A log line (e.g. a handled exception) reported for one FL client."""

    fl_client_name: str
    log: str
