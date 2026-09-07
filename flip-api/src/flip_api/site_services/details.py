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

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.interfaces.site import ISiteDetails
from flip_api.site_services.services.details_service import get_site_details, update_site_details
from flip_api.utils.logger import logger

router = APIRouter(prefix="/site", tags=["site_services"])


# [#114] ✅
@router.get("/details", response_model=ISiteDetails)
def get_details(db: Session = Depends(get_session), user_id: UUID = Depends(verify_token)) -> ISiteDetails:
    """
    Fetch current site details.

    Args:
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        ISiteDetails: Current site details including banner and deployment mode.

    Raises:
        HTTPException: If site details cannot be fetched due to an error.
    """
    try:
        return get_site_details(db)
    except HTTPException:
        # Pass the service's own status and detail through unchanged (e.g. its 404), rather than
        # re-wrapping every one into a 500 that embeds the original message.
        raise
    except Exception as e:
        logger.exception("Error fetching site details")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from e


# [#114] ✅
@router.put("/details", response_model=ISiteDetails)
def update_details(
    site_details: ISiteDetails,
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> ISiteDetails:
    """
    Update site details.

    Args:
        site_details (ISiteDetails): Updated site configuration.
        db (Session): Database session.
        user_id (UUID): User ID from authentication.

    Returns:
        ISiteDetails: Updated site details including banner and deployment mode.

    Raises:
        HTTPException: If site details cannot be updated due to an error.
    """
    if not has_permissions(user_id, [PermissionRef.CAN_MANAGE_SITE_BANNER], db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to update site details",
        )

    try:
        update_site_details(site_details, db)
        return get_site_details(db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating site details")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        ) from e
