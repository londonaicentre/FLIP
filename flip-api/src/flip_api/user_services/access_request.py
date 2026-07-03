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
from uuid import UUID

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlmodel import Session, col, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.user_models import AccessRequest, PermissionRef
from flip_api.domain.interfaces.shared import (
    IAccessRequest,
    IAccessRequestRecord,
    IUpdateAccessRequestStatus,
)
from flip_api.domain.schemas.status import AccessRequestStatus
from flip_api.utils.constants import ACCESS_REQUEST_TEMPLATE_NAME
from flip_api.utils.logger import logger
from flip_api.utils.paging_utils import IPagedData, get_paging_details, get_total_pages

router = APIRouter(prefix="/users", tags=["user_services"])


def _notify_admins_of_access_request(request: IAccessRequest, access_request: AccessRequest, db: Session) -> None:
    """Send the admin-notification email for a persisted access request, best-effort.

    The access request is already durably recorded before this runs, so a failure
    here is logged and swallowed — a broken, sandboxed or throttled SES backend
    (see #592) must never fail the user's submission or lose the request (#699).
    On success the row's ``email_notified`` flag is set so operators can find
    requests that still need a manual follow-up (``WHERE email_notified IS false``).

    Args:
        request (IAccessRequest): The submitted access request (email/name/reason).
        access_request (AccessRequest): The persisted row to flag on success.
        db (Session): The database session used to flag successful notification.

    Returns:
        None
    """
    try:
        sesv2 = boto3.client("sesv2", region_name=get_settings().AWS_REGION)
        template_data = {
            "email": request.email,
            "name": request.full_name,
            "purpose": request.reason_for_access,
        }
        sesv2.send_email(
            FromEmailAddress=get_settings().AWS_SES_SENDER_EMAIL_ADDRESS,
            Destination={"ToAddresses": [get_settings().AWS_SES_ADMIN_EMAIL_ADDRESS]},
            Content={
                "Template": {
                    "TemplateName": ACCESS_REQUEST_TEMPLATE_NAME,
                    "TemplateData": json.dumps(template_data),
                }
            },
        )
        access_request.email_notified = True
        db.add(access_request)
        db.commit()
        logger.info("Access request notification email dispatched to admin")
    except Exception as e:
        # Swallow: the request is already persisted. Covers SES ``ClientError``
        # (unverified sender, sandbox, throttling) and any credential/config
        # error. Operators recover un-notified requests from the DB.
        db.rollback()
        logger.error(f"Access request recorded but admin notification email could not be sent: {e}")


# [#114] ✅  [#699] persist-then-notify so a broken email backend can't lose the request
@router.post("/access", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def request_access(request: IAccessRequest, db: Session = Depends(get_session)) -> None:
    """Record a platform access request and notify administrators.

    The request is persisted first so it is never lost, then an admin
    notification email is sent on a best-effort basis. A failure to send the
    email does NOT fail the submission (#699); a failure to persist does.

    Args:
        request (IAccessRequest): The access request details.
        db (Session): The database session.

    Returns:
        None

    Raises:
        HTTPException: 500 if the access request cannot be persisted.
    """
    logger.info("Received access request")

    access_request = AccessRequest(
        email=request.email,
        full_name=request.full_name,
        reason_for_access=request.reason_for_access,
    )
    try:
        db.add(access_request)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Failed to persist access request")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record access request. Please try again later.",
        ) from e

    _notify_admins_of_access_request(request, access_request, db)


@router.get(
    "/access",
    summary="List access requests",
    description="List platform access requests with pagination. Requires CAN_MANAGE_USERS permission.",
    response_model=IPagedData[IAccessRequestRecord],
    status_code=status.HTTP_200_OK,
)
def list_access_requests(
    request: Request,
    status_filter: AccessRequestStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> IPagedData[IAccessRequestRecord]:
    """List access requests for administrator triage, newest first.

    Args:
        request (Request): FastAPI request object (source of the paging query params).
        status_filter (AccessRequestStatus | None): Optional ``status`` query param to
            return only requests in that lifecycle state (e.g. only ``PENDING``).
        db (Session): The database session.
        token_id (UUID): ID of the authenticated user making the request.

    Returns:
        IPagedData[IAccessRequestRecord]: Paginated access requests, newest first.

    Raises:
        HTTPException: 403 if the caller lacks ``CAN_MANAGE_USERS``; 500 on database error.
    """
    if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
        logger.error(f"User with ID: {token_id} attempted to list access requests without permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised to manage access requests"
        )

    try:
        paging_info = get_paging_details(dict(request.query_params))

        count_stmt = select(func.count()).select_from(AccessRequest)
        data_stmt = select(AccessRequest).order_by(col(AccessRequest.created_at).desc())
        if status_filter is not None:
            count_stmt = count_stmt.where(AccessRequest.status == status_filter)
            data_stmt = data_stmt.where(AccessRequest.status == status_filter)

        total_records = db.exec(count_stmt).one_or_none() or 0
        rows = db.exec(data_stmt.offset(paging_info.offset).limit(paging_info.page_size)).all()

        return IPagedData(
            page=paging_info.page_number,
            page_size=paging_info.page_size,
            total_pages=get_total_pages(total_records, paging_info.page_size),
            total_records=total_records,
            data=[IAccessRequestRecord.model_validate(row) for row in rows],
        )  # type: ignore[call-arg]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing access requests")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e


@router.patch(
    "/access/{access_request_id}",
    summary="Update an access request's status",
    description="Enroll or dismiss an access request. Requires CAN_MANAGE_USERS permission.",
    response_model=IAccessRequestRecord,
    status_code=status.HTTP_200_OK,
)
def update_access_request_status(
    access_request_id: UUID,
    update: IUpdateAccessRequestStatus,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> AccessRequest:
    """Set an access request's lifecycle status (e.g. ENROLLED / DISMISSED).

    Records the acting administrator on the row so triage is auditable. Enrolment
    itself (creating the Cognito user) is a separate step handled by the register-user
    flow; this endpoint only moves the request's status once that has been decided.

    Args:
        access_request_id (UUID): ID of the access request to update.
        update (IUpdateAccessRequestStatus): The new lifecycle status.
        db (Session): The database session.
        token_id (UUID): ID of the authenticated administrator making the request.

    Returns:
        AccessRequest: The updated access request (serialised as IAccessRequestRecord).

    Raises:
        HTTPException: 403 without ``CAN_MANAGE_USERS``; 404 if the request does not exist;
            500 on database error.
    """
    if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], db):
        logger.error(f"User with ID: {token_id} attempted to update an access request without permission")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised to manage access requests"
        )

    try:
        access_request = db.get(AccessRequest, access_request_id)
        if access_request is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")

        access_request.status = update.status
        access_request.handled_by_user_id = token_id
        access_request.updated_at = datetime.utcnow()
        db.add(access_request)
        db.commit()
        db.refresh(access_request)
        return access_request
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"Error updating access request {access_request_id}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from e
