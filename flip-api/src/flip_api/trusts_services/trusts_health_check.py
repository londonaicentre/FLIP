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

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from flip_api.config import get_settings
from flip_api.db.database import get_session
from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import ITrustHealth
from flip_api.utils.logger import logger

router = APIRouter(prefix="/trust", tags=["trusts_services"])


# [#114] ✅
@router.get("/health", status_code=status.HTTP_200_OK, response_model=list[ITrustHealth])
async def check_trusts_health(
    db: Session = Depends(get_session),
) -> list[ITrustHealth]:
    """
    Retrieves health status of all trusts based on their last heartbeat timestamp.

    Instead of making outbound HTTP calls to each trust's /health endpoint, this checks
    the last_heartbeat field updated by the trust's polling service.

    Args:
        db (Session): Database session for querying trusts.

    Returns:
        list[ITrustHealth]: A list of ITrustHealth objects representing the health status of each trust.

    Raises:
        HTTPException: If no trusts are found in the database or if there is an error during the operation.
    """
    try:
        logger.debug("Checking trust health via heartbeat timestamps...")

        statement = select(Trust)
        result = db.exec(statement).all()

        logger.debug(f"Found {len(result)} trusts.")

        if not result:
            logger.warning("No trusts found in the database")
            raise HTTPException(status_code=404, detail="No trusts found")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=get_settings().HEARTBEAT_TIMEOUT_SECONDS)

        response: list[ITrustHealth] = []
        for trust in result:
            # Trust is online if it has sent a heartbeat within the timeout window
            if trust.last_heartbeat is not None:
                last_hb = trust.last_heartbeat
                if last_hb.tzinfo is None:
                    last_hb = last_hb.replace(tzinfo=timezone.utc)
                online = last_hb >= cutoff
            else:
                online = False

            response.append(
                ITrustHealth(trust_id=trust.id, trust_name=trust.name, online=online)  # type: ignore[call-arg]
            )

        logger.info(f"Trust health status: {sum(1 for r in response if r.online)}/{len(response)} online")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking trusts health: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
