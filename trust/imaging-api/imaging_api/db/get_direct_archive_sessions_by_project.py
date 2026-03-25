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


from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from imaging_api.db.models import DirectArchiveSession, DirectArchiveSessionORM
from imaging_api.utils.logger import logger


async def get_direct_archive_sessions_by_project(project_id: str, session: AsyncSession) -> list[DirectArchiveSession]:
    """
    Get all direct archive sessions for a given project.
    This function queries the database for all direct archive sessions associated with the specified project ID.

    Args:
        project_id (str): The ID of the project for which to retrieve direct archive sessions.
        session (AsyncSession): The SQLAlchemy async session to use for the database query.

    Returns:
        List[DirectArchiveSession]: A list of DirectArchiveSession objects representing the
        sessions found for the project.

    Raises:
        HTTPException: If project_id is empty.
    """
    if not project_id.strip():
        raise HTTPException(status_code=400, detail="project_id cannot be empty")

    logger.debug(f"Querying sessions for project {project_id}")
    stmt = (
        select(DirectArchiveSessionORM)
        .where(DirectArchiveSessionORM.project == project_id)
        .order_by(DirectArchiveSessionORM.timestamp.desc())
    )

    result = await session.execute(stmt)
    sessions = result.scalars().all()

    logger.info(f"Found {len(sessions)} direct archive sessions for project {project_id}")

    return [DirectArchiveSession.from_orm(s) for s in sessions]
