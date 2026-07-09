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

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FLLogEvent(StrEnum):
    """Typed FL progress events for ``POST /model/{id}/logs``.

    The FL layer reports **facts** (event type + structured details); display
    text is composed hub-side at serve time, so wording changes are a flip-api
    redeploy and never an FL-image rebuild. Mirrors flip-api's
    ``domain/schemas/types.py::FLLogEvent``.

    Rounds are 1-based on every event, on both backends (NVFLARE's internal
    ``_current_round`` is 0-based and must be normalised before sending).
    """

    ROUND_STARTED = "ROUND_STARTED"
    CLIENT_RESULT_RECEIVED = "CLIENT_RESULT_RECEIVED"
    ROUND_AGGREGATED = "ROUND_AGGREGATED"


class TrainingMetrics(BaseModel):
    """A single training/evaluation metric value reported for one FL client.

    ``fl_client_name`` is the FL client's identity as the FL server sees it —
    the FL participant name for NVFLARE, the SUPERNODE_NAME for Flower. The hub resolves
    it to a trust before storing the metric.
    """

    fl_client_name: str
    global_round: int = Field(ge=0)
    label: str
    result: float


class TrainingLog(BaseModel):
    """One row for ``POST /model/{id}/logs``: free text XOR a typed round event.

    Mirrors flip-api's ``domain/schemas/private.py::TrainingLog`` — keep in sync.
    Free-text rows (``log`` set) carry exception reports verbatim; typed event
    rows (``event_type`` set) carry round-progress facts. ``fl_client_name`` is
    ``None`` for hub-attributed rows (e.g. ``ROUND_STARTED`` from the fl-server's
    own control flow).
    """

    fl_client_name: str | None = None
    log: str | None = None
    event_type: FLLogEvent | None = None
    # 1-based on both backends; every event in the vocabulary is round-scoped.
    global_round: int | None = Field(default=None, ge=1)
    details: dict[str, Any] | None = None
    success: bool = True

    @model_validator(mode="after")
    def _log_xor_event(self) -> "TrainingLog":
        if (self.log is None) == (self.event_type is None):
            raise ValueError("Exactly one of 'log' and 'event_type' must be set")
        if self.event_type is not None and self.global_round is None:
            raise ValueError("'global_round' is required when 'event_type' is set")
        return self
