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

from pydantic import BaseModel, Field


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
    """A log line (e.g. a handled exception) reported for one FL client."""

    fl_client_name: str
    log: str
