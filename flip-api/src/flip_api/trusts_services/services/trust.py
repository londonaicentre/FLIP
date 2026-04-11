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

# app/crud.py
from uuid import UUID

from sqlmodel import Session, col, select

from flip_api.db.models.main_models import Trust
from flip_api.domain.interfaces.trust import ITrust
from flip_api.utils.logger import logger


def get_trusts(session: Session, ids: list[UUID] | None = None) -> list[ITrust]:
    """
    Retrieve a list of Trusts from the database.
    If IDs are provided, only those Trusts will be returned.
    If no IDs are provided, all Trusts will be returned.

    Args:
        session (Session): The SQLModel session to use for the query.
        ids (Optional[List[UUID]]): A list of Trust IDs to filter by. If None, all Trusts are returned.

    Returns:
        List[ITrust]: A list of Trust objects.

    Raises:
        ValueError: If no Trusts are found or if the database response is empty.
    """
    logger.debug("Attempting to get the list of trusts...")

    if ids:
        logger.debug(f"List of IDs passed in: {ids}")
        statement = select(Trust).where(col(Trust.id).in_(ids))
    else:
        logger.debug("No IDs passed. Retrieving all trusts...")
        statement = select(Trust)

    result = session.exec(statement).all()

    if not result:
        raise ValueError("No database response returned")

    logger.info(f"Number of trusts: {len(result)}")

    # Convert SQLModel Trust objects to ITrust interface
    trusts = [
        ITrust(
            id=trust.id,
            name=trust.name,
        )
        for trust in result
    ]

    return trusts
