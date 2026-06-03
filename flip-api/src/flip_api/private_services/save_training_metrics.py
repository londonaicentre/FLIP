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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.access_manager import authenticate_internal_service
from flip_api.db.database import get_session
from flip_api.domain.schemas.private import TrainingMetrics
from flip_api.model_services.services.model_service import resolve_trust_from_fl_client_name, validate_trust_ids
from flip_api.private_services.services.private_service import save_training_metrics
from flip_api.utils.logger import logger

router = APIRouter(tags=["private_services"])


# [#114] ✅
@router.post(
    "/model/{model_id}/metrics",
    summary="Save training metrics for a model from a specific FL client.",
    status_code=status.HTTP_204_NO_CONTENT,  # Returns 204 No Content on success
    response_model=None,
)
def save_training_metrics_endpoint(
    model_id: UUID,
    training_metrics: TrainingMetrics,
    request: Request,
    db: Session = Depends(get_session),
    _: None = Depends(authenticate_internal_service),
) -> None:
    """
    Receives and saves training metrics for a given model ID and FL client.

    This endpoint is internal-only: it accepts requests from the fl-server on the
    Central Hub (authenticated via INTERNAL_SERVICE_KEY_HEADER).

    Args:
        model_id (UUID): The unique identifier for the model.
        training_metrics (TrainingMetrics): The training metrics to be saved.
        request (Request): The FastAPI request object, used for logging and context.
        db (Session): Database session dependency.

    Returns:
        Response: HTTP 204 No Content on success, or appropriate error response.

    Raises:
        HTTPException: If the trust is not associated with the model.
        HTTPException: If an internal server error occurs during processing.
    """

    # training_metrics.fl_client_name is the FL client's identity as the FL server reports it — the FL kit slot for
    # NVFLARE, the SUPERNODE_NAME for Flower. resolve_trust_from_fl_client_name (below) maps it to the owning Trust so
    # the metric is validated and stored against the trust, not the raw client name.
    # The two backends report this name differently — see issue #538.
    # NOTE this worked before because the trust name was the same as the FL client name (i.e. Trust_1, Trust_2, etc.).

    fl_client_name = training_metrics.fl_client_name

    logger.debug(f"Received request to save training metrics for model {model_id} from FL client {fl_client_name}")

    try:
        # The FL client name the FL server reports differs per FL_BACKEND (NVFLARE: the FL kit slot;
        # Flower: SUPERNODE_NAME). resolve_trust_from_fl_client_name hides that discrepancy and
        # returns the trust to validate against the model's approved trusts — see issue #538.
        trust = resolve_trust_from_fl_client_name(fl_client_name, db)
        if trust is None:
            error_msg = f"FL client '{fl_client_name}' could not be resolved to a trust (model: {model_id})"
            logger.error(error_msg)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        if not validate_trust_ids(model_id=model_id, trust_ids=[trust.id], session=db):
            error_msg = f"The trust: {trust.name} is not associated with model: {model_id}"
            logger.error(error_msg)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        save_training_metrics(model_id=model_id, trust=trust, training_metrics=training_metrics, db=db)

    except HTTPException as http_exc:
        logger.warning(
            f"Service HTTPException for model {model_id}, FL client {training_metrics.fl_client_name}:{http_exc.detail}"
        )
        raise http_exc
    except Exception as e:
        logger.error(
            f"Unhandled error processing training metrics for model {model_id}, "
            f"FL client {training_metrics.fl_client_name}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while saving training metrics.",
        )
