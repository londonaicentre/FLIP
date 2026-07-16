# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

TrimStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]

NonEmptyUUIDList = Annotated[list[UUID], Field(min_length=1)]


class FLLogEvent(StrEnum):
    """Typed FL progress events accepted by ``POST /model/{id}/logs``.

    The FL layer reports **facts** (event type + structured fields); the display
    text is composed hub-side at serve time (``log_rendering.py``), so wording
    changes are a flip-api redeploy and never an FL-image rebuild. Stored in the
    plain-text ``fl_logs.event_type`` column — deliberately NOT a native PG enum,
    so extending this vocabulary never needs an ``ALTER TYPE`` migration.

    Rounds are 1-based on every event, on both backends (NVFLARE's internal
    ``_current_round`` is 0-based and is normalised at the emission boundary).
    """

    ROUND_STARTED = "ROUND_STARTED"
    CLIENT_RESULT_RECEIVED = "CLIENT_RESULT_RECEIVED"
    ROUND_AGGREGATED = "ROUND_AGGREGATED"


class FLBackend(StrEnum):
    """The set of supported federated-learning backends.

    Defined once here so the value flows consistently through the DB column
    (``FLNets.fl_backend``), the FL interfaces, and every helper that
    resolves/bundles/pulls per backend. Add a new backend in this one place.

    ``StrEnum`` (not a bare ``Enum``) so each member *is* its lowercase string
    value: ``str(FLBackend.FLOWER) == "flower"``. Env (``FL_BACKEND``), JSON API
    fields and S3 path segments all use that plain lowercase value, exactly as the
    previous ``Literal`` did. The ``FLNets.fl_backend`` column is an auto-mapped
    SQLAlchemy ``Enum`` (like the other enum columns) and so persists the member
    *name* (``NVFLARE``/``FLOWER``); reads return a real ``FLBackend`` member.
    """

    NVFLARE = "nvflare"
    FLOWER = "flower"
