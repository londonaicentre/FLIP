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

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator, validator

from flip_api.config import get_settings
from flip_api.domain.schemas.status import TaskType
from flip_api.domain.schemas.types import FLLogEvent
from flip_api.utils.constants import DEFAULT_X_AXIS_LABEL


class Results(BaseModel):
    value: str
    count: int


class OmopData(BaseModel):
    name: str
    results: list[Results]


class OmopCohortResults(BaseModel):
    query_id: UUID
    trust_id: UUID
    created: str
    record_count: int
    data: list[OmopData]
    # Populated by trust-api when the cohort query fails (data-access-api error,
    # decryption failure, etc.). When set, the hub records the trust as errored
    # instead of leaving its per-trust UI status stuck on "running".
    error: str | None = None
    # True when the trust privacy-suppressed a below-threshold count. A genuine zero is
    # suppressed identically, so this never reveals whether >=1 patient matched. Set by
    # data-access-api and forwarded verbatim by trust-api. Lets the UI show a
    # "below-threshold" chip instead of a literal 0 that reads as "no data available".
    # See issue #519.
    suppressed: bool = False

    @validator("data", pre=True, always=True)
    def ensure_data_is_list(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        return value


class TrainingMetrics(BaseModel):
    """One FL training/evaluation metric point. Mirrors flip-utils' ``flip/schemas.py`` (the sender).

    ``global_round`` is provenance — always the FL global round the metric was reported in, never
    overridden. The plot coordinate is the (``x_label``, ``x_value``) pair, defaulting to the global
    round on the "Global Rounds" axis — see FLIP#148.
    """

    fl_client_name: str
    global_round: int = Field(ge=0)
    label: str
    # nan/inf are rejected here and on x_value below: a non-finite value would survive to the DB and
    # then break JSON-encoding the metrics response for the whole model.
    result: float = Field(allow_inf_nan=False)
    # The x-coordinate this metric is plotted at. Defaults to the global round (see the validator
    # below), which also keeps payloads from pre-x_value FL images valid.
    x_value: float = Field(allow_inf_nan=False)
    # Label naming the x-axis this metric is plotted against; defaults to the FL global round axis when
    # the client doesn't send one. A plot's identity is the (label, x_label) pair — see FLIP#148.
    # Bounded like event_type: the label originates in uploaded training code, is persisted, mints a
    # new plot per distinct value, and is rendered as the axis title — unbounded or empty values would
    # mint unbounded or blank-titled plots.
    x_label: str = Field(default=DEFAULT_X_AXIS_LABEL, min_length=1, max_length=64)

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
    """One row for ``POST /model/{id}/logs``: free text XOR a typed round event.

    Mirrored in ``flip-utils/flip/schemas.py`` (the FL-side sender) — keep the two
    in sync. Two mutually exclusive shapes:

    - **Free text** (``log`` set): exception reports and legacy messages. The text
      is stored and served verbatim.
    - **Typed event** (``event_type`` set): a round-progress fact. Display text is
      composed hub-side at serve time, so the FL layer never bakes in wording.

    ``fl_client_name`` is the FL kit slot when the row is trust-attributed, or
    ``None`` for hub-attributed rows (e.g. ``ROUND_STARTED``). Old FL images keep
    sending the original ``{fl_client_name, log}`` shape — every new field is
    optional with a compatible default.
    """

    fl_client_name: str | None = None
    log: str | None = None
    # Senders use the FLLogEvent vocabulary, but the field is plain validated text
    # end-to-end (like the fl_logs column): a newer FL image's event is stored and
    # served via the unknown-event render fallback, never rejected at ingest. The
    # one exception is the hub-reserved QUEUE_POSITION, refused below: a spoofed
    # row would render in the feed and could perturb the FL scheduler's
    # emit-on-change dedup (which compares against any stored row).
    event_type: str | None = Field(default=None, max_length=64)
    # 1-based on both backends; every event this endpoint accepts is round-scoped
    # (the hub-emitted, round-less QUEUE_POSITION is written directly by the FL
    # scheduler and rejected at this boundary). The ceiling is the PG INTEGER max
    # of the fl_logs.global_round column — without it an oversized round passes
    # validation and 500s at insert.
    global_round: int | None = Field(default=None, ge=1, le=2_147_483_647)
    # Bounded by _bound_details below — same defence-in-depth rationale as the caps on
    # event_type and global_round: flip.send_event is reachable from uploaded server-side
    # app code, and details is persisted verbatim into JSONB per event row.
    details: dict[str, Any] | None = None
    success: bool = True

    @model_validator(mode="after")
    def _log_xor_event(self) -> "TrainingLog":
        if (self.log is None) == (self.event_type is None):
            raise ValueError("Exactly one of 'log' and 'event_type' must be set")
        if self.event_type is not None and not self.event_type.strip():
            raise ValueError("'event_type' must be non-blank when set")
        if self.event_type == FLLogEvent.QUEUE_POSITION:
            raise ValueError("'QUEUE_POSITION' is emitted by the hub's FL scheduler and cannot be ingested")
        if self.event_type is not None and self.global_round is None:
            raise ValueError("'global_round' is required when 'event_type' is set")
        return self

    @model_validator(mode="after")
    def _bound_details(self) -> "TrainingLog":
        # json.dumps escapes non-ASCII by default, so len() counts bytes. The known event
        # vocabularies need < 100 bytes; 8192 leaves room for future facts while keeping a
        # hostile payload from bloating the fl_logs JSONB column.
        if self.details is not None and len(json.dumps(self.details, default=str)) > 8192:
            raise ValueError("'details' must serialize to at most 8192 bytes")
        return self


class ProjectApprovalBody(BaseModel):
    trusts: list[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class ProjectApproval(BaseModel):
    project_id: UUID = Field(..., description="Project ID to be approved.")
    trust_ids: list[UUID] = Field(..., description="List of Trust IDs to be approved for the project.")


class TrustSpecificData(BaseModel):  # Parsed from query_result.data JSON string
    record_count: int
    data: list[OmopData]
    error: str | None = None
    # Mirrors OmopCohortResults.suppressed; persisted in QueryResult.data so the
    # aggregator can flag privacy-suppressed trusts. See issue #519.
    suppressed: bool = False


class AggregatedTrustFieldResult(BaseModel):
    data: Any
    trust_name: str
    trust_id: str


class AggregatedFieldResult(BaseModel):
    name: str  # Field name
    results: list[AggregatedTrustFieldResult]


class AggregatedCohortStats(BaseModel):  # Stored as JSON in query_stats.stats
    record_count: int
    trusts_results: list[AggregatedFieldResult]
    # trust_id (str) -> record_count for trusts that responded successfully.
    # Distinguishes "responded with 0 (privacy-suppressed)" from "never
    # responded" so the UI shows 0 instead of staying stuck on "running".
    trust_record_counts: dict[str, int] = Field(default_factory=dict)
    # trust_id (str) -> error message for trusts whose cohort query failed.
    # Mutually exclusive with trust_record_counts for the same trust.
    trust_errors: dict[str, str] = Field(default_factory=dict)
    # trust_ids (str) that privacy-suppressed their count (below threshold).
    # A suppressed trust still appears in trust_record_counts with count 0 — this
    # list tells the UI to render a "suppressed" chip instead of a literal 0 that
    # would read as "no data available". See issue #519.
    trust_suppressed: list[str] = Field(default_factory=list)


# Helper structure for data fetched from DB for aggregation
class FetchedAggregationData(BaseModel):
    trust_name: list[str]
    trust_id: list[str]
    data: list[str]  # List of JSON strings, each is a TrustSpecificData


class TrustTaskResponse(BaseModel):
    """Response model for a single trust task."""

    id: UUID
    task_type: TaskType
    payload: str
    created_at: datetime


class TaskResultInput(BaseModel):
    """Input model for submitting a task result."""

    success: bool
    result: str | None = Field(default=None, max_length=get_settings().MAX_TASK_RESULT_LENGTH)


class ServiceHealthStatus(StrEnum):
    """Wire vocabulary for a trust-internal service's probed state."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ServiceHealthEntry(BaseModel):
    """One probed service inside a heartbeat snapshot.

    Bounds are defence-in-depth: the payload originates inside a trust but is
    persisted verbatim into JSONB and rendered on the Connection Status page, so
    every free-text field is length-capped and numerics are range-capped.
    """

    status: ServiceHealthStatus
    version: str | None = Field(default=None, max_length=64)
    response_ms: int | None = Field(default=None, ge=0, le=1_000_000)


# Roster keys are the trust collector's wire contract; the hub stays roster-agnostic
# and only constrains the shape, so a trust can report a new service without a hub deploy.
_ServiceKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,32}$")]


class TrustHeartbeatInput(BaseModel):
    """Optional heartbeat body: the trust's per-service health snapshot (issue #901).

    Older trust-api builds POST no body at all — the route treats an absent body as
    "no snapshot" and only stamps ``last_heartbeat``. ``collected_at`` is accepted
    (and discarded) so collector payloads stay self-describing on the wire; it is
    never persisted or used for staleness — the hub stamps its own
    ``services_health_at`` on receipt so a trust cannot backdate or postdate its
    snapshot.
    """

    # Bounds declared on the field so they also surface in the OpenAPI schema;
    # entry-level caps live on ServiceHealthEntry. min_length matters as much as
    # max: an empty snapshot would overwrite a good one AND be stamped fresh,
    # defeating the collector's drop-the-snapshot-on-failure design.
    services: dict[_ServiceKey, ServiceHealthEntry] = Field(min_length=1, max_length=16)
    collected_at: datetime | None = None
