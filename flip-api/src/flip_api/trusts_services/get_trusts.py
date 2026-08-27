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

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func
from sqlmodel import Session, col, select

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import ProjectTrustIntersect, Trust
from flip_api.domain.interfaces.trust import ITrustStatus
from flip_api.domain.schemas.private import ServiceHealthEntry
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])

_service_entries_adapter = TypeAdapter(dict[str, ServiceHealthEntry])


def _as_utc_iso(value: datetime | None) -> str | None:
    """Serialise a naive UTC datetime as an ISO-8601 string with a `Z` marker.

    The Trust columns are `timestamp without time zone` but values are written
    via `datetime.now(timezone.utc)` — so they're already UTC, the timezone is
    just stripped on persist. Tagging the wire format prevents the browser from
    treating the naive ISO as local time.

    Args:
        value (datetime | None): A naive (or aware) UTC datetime, or None.

    Returns:
        str | None: ISO-8601 string with millisecond precision and a trailing
        `Z`, or None when ``value`` is None.
    """
    if value is None:
        return None

    return value.isoformat(timespec="milliseconds") + "Z"


def _as_service_entries(raw: dict | None, trust_name: str) -> dict[str, ServiceHealthEntry] | None:
    """Re-parse a stored health snapshot into the wire model it was written with.

    The snapshot was validated by ``TrustHeartbeatInput`` (same entry model) on
    receipt and persisted verbatim, so this normally always succeeds. It is
    guarded anyway because the response field is typed: a row that no longer
    parses — a hub rolled back past a vocabulary change, or hand-edited JSONB —
    would otherwise fail the whole list, and this endpoint also backs every
    trust picker. Dropping just that snapshot degrades the one row to the UI's
    existing "No data" state.

    Args:
        raw (dict | None): The persisted ``trust.services_health`` JSONB value.
        trust_name (str): Trust name, for the discard log line.

    Returns:
        dict[str, ServiceHealthEntry] | None: The parsed snapshot, or None when
        the trust has never reported one or the stored value no longer parses.
    """
    if not raw:
        return None

    try:
        return _service_entries_adapter.validate_python(raw)
    except ValidationError:
        logger.warning(f"Discarding unparseable services_health snapshot for trust '{trust_name}'", exc_info=True)
        return None


# [#114][#609] Single trust-list endpoint — authenticated, NOT admin-gated.
@router.get("", response_model=list[ITrustStatus], status_code=status.HTTP_200_OK)
def get_trusts(
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[ITrustStatus]:
    """List every trust with its connection status.

    The single list-of-trusts endpoint: it powers the trust pickers (project
    staging, cohort query, charts) and the Connection Status page (trust table +
    topology). Readable by any authenticated user (Researcher, Viewer, Admin) —
    the fields are benign trust metadata (name, code, region, heartbeat, project
    count) with no secrets, so this is deliberately not admin-gated. Creating a
    trust (``POST /admin/trusts``) and the plaintext keys it returns stay
    admin-only.

    Issue #901 added ``services``/``services_updated_at`` (per-service status,
    version and probe latency) to that set, and the Connection Status page is
    itself non-admin. The versions are the trust's own service inventory rather
    than a secret — a trust exposes no inbound ports, so they are unreachable
    except from inside the trust, where XNAT's version is anonymously readable
    anyway — but note this widens the non-admin surface beyond the list above.

    Args:
        db (Session): Database session, provided by dependency injection.
        user_id (UUID): ID of the authenticated user making the request.

    Returns:
        list[ITrustStatus]: Every trust with name, code, region, last_heartbeat,
        and project_count (count of rows in ``project_trust_intersect``).

    Raises:
        HTTPException: 500 on database error.
    """
    try:
        trusts = db.exec(select(Trust)).all()

        # One round-trip to compute per-trust project counts.
        count_rows = db.exec(
            select(ProjectTrustIntersect.trust_id, func.count(col(ProjectTrustIntersect.id))).group_by(
                col(ProjectTrustIntersect.trust_id)
            )
        ).all()
        counts: dict[UUID, int] = {row[0]: row[1] for row in count_rows if row[0] is not None}

        return [
            ITrustStatus(
                id=t.id,
                name=t.name,
                code=t.code,
                region=t.region,
                last_heartbeat=_as_utc_iso(t.last_heartbeat),
                project_count=counts.get(t.id, 0),
                services=_as_service_entries(t.services_health, t.name),
                services_updated_at=_as_utc_iso(t.services_health_at),
            )
            for t in trusts
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing trusts")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from e
