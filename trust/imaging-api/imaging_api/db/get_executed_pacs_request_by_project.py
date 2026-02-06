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

from typing import List

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from imaging_api.db.models import ExecutedPacsRequest, ExecutedPacsRequestORM
from imaging_api.utils.logger import logger


async def get_executed_pacs_request_by_project(project_id: str, session: AsyncSession) -> List[ExecutedPacsRequest]:
    """
    Get all executed PACS requests for a given project.

    Args:
        project_id (str): The ID of the project.
        session (AsyncSession): The database session.

    Returns:
        List[ExecutedPacsRequest]: A list of executed PACS requests.
    """
    if not project_id.strip():
        raise HTTPException(status_code=400, detail="project_id cannot be empty")

    logger.debug(f"Querying sessions for project {project_id}")
    stmt = (
        select(ExecutedPacsRequestORM)
        .where(ExecutedPacsRequestORM.xnat_project == project_id)
        .order_by(ExecutedPacsRequestORM.timestamp.desc())
    )

    result = await session.execute(stmt)
    sessions = result.scalars().all()

    logger.info(f"Found {len(sessions)} executed PACS requests for project {project_id}")

    return [ExecutedPacsRequest.from_orm(s) for s in sessions]
