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

import psycopg2
from sqlmodel import Session, delete, select

from flip_api.db.database import engine
from flip_api.db.models.user_models import Permission, PermissionRef, Role, RolePermission
from flip_api.utils.logger import logger


def seed_role_permissions(session: Session) -> None:
    """Seed role permissions intersections."""
    # Delete existing role permissions
    delete(RolePermission)
    session.commit()

    admin_role = session.exec(select(Role.id).where(Role.name == "Admin")).first()

    if admin_role:
        # Get all permissions
        permissions = session.exec(select(Permission)).all()

        # Create role-permission intersections for admin
        for permission in permissions:
            try:
                role_perm = RolePermission(role_id=admin_role, permission_id=permission.id)
                session.add(role_perm)
                session.commit()
            except psycopg2.IntegrityError:
                session.rollback()
                logger.debug(
                    f"RolePermission for role_id {admin_role} and permission_id {permission.id} already exists."
                )
    else:
        logger.debug("Admin role not found. Cannot seed role permissions.")

    # Create default permissions for the Researcher role
    researcher_role = session.exec(select(Role.id).where(Role.name == "Researcher")).first()

    if researcher_role:
        # Define default permissions for the Researcher role
        default_permissions: list[str] = [
            PermissionRef.CAN_MANAGE_PROJECTS.value,
        ]
        for permission_id in default_permissions:
            if permission_id:
                try:
                    role_perm = RolePermission(role_id=researcher_role, permission_id=UUID(permission_id))
                    session.add(role_perm)
                    session.commit()
                except psycopg2.IntegrityError:
                    session.rollback()
                    logger.debug(
                        f"RolePermission for role_id {researcher_role} and permission {permission_id} already exists."
                    )
            else:
                logger.debug(f"Permission {permission_id} not found. Cannot assign to Researcher role.")
    logger.info("Role permissions seeded successfully.")


if __name__ == "__main__":
    with Session(engine) as session:
        seed_role_permissions(session)
        logger.info("Role permissions seeding completed.")
        session.close()
        logger.info("Session closed.")
