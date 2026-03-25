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

from sqlmodel import Session, select

from flip_api.db.models.main_models import XNATImageStatus, XNATProjectStatus


def update_status(
    trust_id: UUID,
    xnat_project_id: UUID,
    project_id: UUID,
    status: XNATImageStatus,
    db: Session,
) -> int:
    """
    Update the status of an existing XNAT project

    Args:
        trust_id (str): ID of the trust
        xnat_project_id (str): ID of the XNAT project
        project_id (str): ID of the project
        status (XNATImageStatus): Status to set
        db (Session): Database session

    Returns:
        int: Number of rows updated
    """
    stmt = select(XNATProjectStatus).where(
        XNATProjectStatus.trust_id == trust_id,
        XNATProjectStatus.xnat_project_id == xnat_project_id,
        XNATProjectStatus.project_id == project_id,
    )

    result = db.exec(stmt).first()

    if result:
        result.retrieve_image_status = status
        db.add(result)
        db.commit()
        # TODO this was left for backwards compatibility, but could be reviewed to return the updated object instead
        return 1

    # TODO this was left for backwards compatibility, but could be reviewed to return an exception instead
    # if the record was not found
    return 0


def insert_status(
    trust_id: UUID,
    xnat_project_id: UUID,
    project_id: UUID,
    status: XNATImageStatus,
    db: Session,
    query_id: UUID | None = None,
) -> int:
    """
    Insert a new XNAT project status record

    Args:
        trust_id (str): ID of the trust
        xnat_project_id (str): ID of the XNAT project
        project_id (str): ID of the project
        status (XNATImageStatus): Status to set
        db (Session): Database session
        query_id (Optional[str]): Optional query ID

    Returns:
        int: Number of rows inserted
    """
    new_status = XNATProjectStatus(
        trust_id=trust_id,
        xnat_project_id=xnat_project_id,
        project_id=project_id,
        retrieve_image_status=status,
        query_at_creation=query_id if isinstance(query_id, UUID) else UUID(query_id) if query_id else None,
    )

    db.add(new_status)
    db.commit()
    db.refresh(new_status)  # This populates fields like defaults, triggers, etc.

    # TODO this was left for backwards compatibility, but could be reviewed to return the new_status object instead
    return 1  # Since we inserted one row
