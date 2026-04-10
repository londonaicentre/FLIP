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

from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from imaging_api.config import get_settings
from imaging_api.utils.auth import get_xnat_auth_headers
from imaging_api.utils.logger import logger

XNATAuthHeaders = Annotated[dict[str, str], Depends(get_xnat_auth_headers)]

# Database connection
XNAT_DATABASE_URL = get_settings().XNAT_DATABASE_URL
logger.info(f"Database URL: {XNAT_DATABASE_URL}")
engine = create_async_engine(XNAT_DATABASE_URL, echo=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# Dependency: get DB session
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an asynchronous database session."""
    async with SessionLocal() as session:
        yield session
