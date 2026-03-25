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

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import IBasicTrust

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# [#114] ✅
@router.get("", status_code=status.HTTP_200_OK, response_model=list[IBasicTrust])
def get_trusts(
    db: Session = Depends(get_session),
    user_id: UUID = Depends(verify_token),
) -> list[IBasicTrust]:
    """
    Retrieve all trusts with their ID and name.

    Raises:
        HTTPException: If an error occurs while fetching trusts from the database.
    """
    try:
        # Using SQLModel select
        statement = select(Trust)
        results = db.exec(statement).all()

        # Convert results to a list of BasicTrust
        trusts = [IBasicTrust(id=result.id, name=result.name) for result in results]
        return trusts

    except Exception as e:
        # Log the error here if needed
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
