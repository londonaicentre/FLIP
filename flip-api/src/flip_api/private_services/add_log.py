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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.domain.schemas.private import TrainingLog
from flip_api.model_services.services.model_service import (
    add_log,
    resolve_trust_from_fl_client_name,
    validate_trust_ids,
)
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


# [#114] ✅
@router.post("/model/{model_id}/logs", response_model=dict[str, str])
def add_log_endpoint(
    model_id: UUID,
    training_log: TrainingLog,
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> dict[str, str]:
    """
    Add a log entry to the database for a specific model.

    This endpoint is internal-only: it accepts requests from the fl-server on the
    Central Hub (authenticated via INTERNAL_SERVICE_KEY_HEADER). The payload is
    either a free-text row (``log`` set) or a typed round event (``event_type``
    set) — ``TrainingLog`` enforces the two shapes.

    Args:
        model_id (UUID): The ID of the model.
        training_log (TrainingLog): The log entry to be added.
        db (Session): The database session.

    Returns:
        dict[str, str]: A confirmation message indicating the log entry was created.

    Raises:
        HTTPException: 400 when the sender cannot be attributed (unknown FL client
            name, or the resolved trust is not associated with the model) — except
            for failed free-text rows, which are stored model-level instead so an
            error report is never dropped; 500 on an internal server error.
    """
    fl_client_name = training_log.fl_client_name

    try:
        # A null fl_client_name means the row is hub-attributed (e.g. a ROUND_STARTED
        # event from the fl-server's own control flow) — there is no slot to resolve.
        trust = None
        if fl_client_name is not None:
            # The FL client name the FL server reports differs per FL_BACKEND (NVFLARE: the FL kit slot;
            # Flower: SUPERNODE_NAME). resolve_trust_from_fl_client_name hides that discrepancy and
            # returns the trust to validate against the model's approved trusts — see issue #538.
            trust = resolve_trust_from_fl_client_name(fl_client_name, db)
            error_msg = None
            # Shown to the user on the fallback row: "unknown slot" and "wrong
            # model" point at different operator fixes, and hub logs aren't
            # visible to them.
            attribution_failure = None
            if trust is None:
                error_msg = f"FL client '{fl_client_name}' could not be resolved to a trust (model: {model_id})"
                attribution_failure = "unknown FL kit slot"
            elif not validate_trust_ids(model_id=model_id, trust_ids=[trust.id], session=db):
                error_msg = f"The trust: {trust.name} is not associated with model: {model_id}"
                attribution_failure = "not associated with this model"

            if error_msg is not None:
                logger.error(error_msg)
                if training_log.log is not None and not training_log.success:
                    # An error report is the one payload that must never be dropped:
                    # uploaded apps control the reported site name, and a 400 here
                    # would strand the traceback in fl-server container logs while
                    # the user's model sits red. Keep it model-level, naming the
                    # unattributable sender in the text. Typed events still 400 —
                    # they carry no traceback, and a misattributed count is worse
                    # than a rejected one.
                    add_log(
                        model_id=model_id,
                        log=f"[unattributed client '{fl_client_name}' — {attribution_failure}] {training_log.log}",
                        session=db,
                        success=False,
                        trust=None,
                        fl_client_name=fl_client_name,
                    )
                    return {"detail": "Created"}
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        add_log(
            model_id=model_id,
            log=training_log.log,
            session=db,
            success=training_log.success,
            trust=trust,
            fl_client_name=fl_client_name,
            event_type=training_log.event_type,
            global_round=training_log.global_round,
            details=training_log.details,
        )

        return {"detail": "Created"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error in add_log endpoint for model {model_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while adding the log.",
        )
