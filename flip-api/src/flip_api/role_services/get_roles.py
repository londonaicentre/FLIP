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
from sqlmodel import Session, select

from flip_api.auth.auth_utils import has_permissions
from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.user_models import PermissionRef, Role
from flip_api.domain.interfaces.role import IRole, IRolesResponse
from flip_api.utils.logger import logger

router = APIRouter(prefix="/roles", tags=["role_services"])


# [#114] ✅
@router.get("/", response_model=IRolesResponse)
def get_roles(
    session: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
) -> IRolesResponse:
    # Check permissions
    if not has_permissions(token_id, [PermissionRef.CAN_MANAGE_USERS], session):
        error_msg = f"User with ID: {token_id} does not have permission"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)

    # Execute query
    query = select(Role.id, Role.name, Role.description).order_by(Role.name)
    try:
        result = session.exec(query)
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

    roles = [
        IRole(
            id=row[0],
            name=row[1],
            description=row[2],
        )  # type: ignore[call-arg]
        for row in result
    ]
    response = IRolesResponse(roles=roles)

    logger.info(f"Output: {response}")
    return response
