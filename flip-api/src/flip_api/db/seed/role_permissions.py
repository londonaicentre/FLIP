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

from flip_api.db.database import get_engine
from flip_api.db.models.user_models import (
    TRUST_SCOPED_PERMISSIONS,
    Permission,
    PermissionRef,
    Role,
    RolePermission,
)
from flip_api.db.seed.seed_logger import logger


def _grant_permissions(session: Session, role_id: UUID, permission_ids: list[UUID]) -> None:
    """Grant a role a set of permissions, skipping pairs already present.

    Matches the check-then-insert idempotency pattern used by ``seed_roles``
    and ``seed_permissions``: avoids relying on IntegrityError recovery and
    stays DB-driver agnostic.

    Args:
        session (Session): Database session.
        role_id (UUID): Role receiving the permissions.
        permission_ids (list[UUID]): Permissions to grant.

    Returns:
        None
    """
    for permission_id in permission_ids:
        existing = session.exec(
            select(RolePermission)
            .where(RolePermission.role_id == role_id)
            .where(RolePermission.permission_id == permission_id)
        ).first()
        if existing:
            continue
        session.add(RolePermission(role_id=role_id, permission_id=permission_id))
    session.commit()


def seed_role_permissions(session: Session) -> None:
    """Seed role/permission intersections.

    Idempotent: running against a populated DB inserts only the missing
    pairs. Does not remove permissions that have been taken out of the
    seed (that would need an explicit migration, not a seed).

    - Admin: every permission defined in ``PermissionRef`` EXCEPT the
      trust-scoped ones (see below).
    - Researcher: ``CAN_CREATE_PROJECTS`` only. ``CAN_MANAGE_PROJECTS`` is
      reserved for Admin — it bypasses per-project access checks (see issue #358).
    - Viewer: none — read-only access is enforced at the route layer by
      the absence of ``CAN_MANAGE_PROJECTS``.
    - Trust Owner: the trust-scoped permissions. The grant is global here
      (``role_permission`` has no trust dimension); what binds authority to a
      single trust is the ``user_role.trust_id`` on the *user's* grant, checked
      by ``has_trust_permissions``.

    Trust-scoped permissions are withheld from Admin deliberately (FLIP#1260).
    Hub-wide administration is not trust authority: granting them to Admin would
    give every hub administrator approval rights over every trust's data, which is
    the hole FLIP#1258 exists to close. ``has_trust_permissions`` already ignores
    global grants, so this is the second of two independent guards — belt and braces
    on the one rule that must not fail open.

    Args:
        session (Session): Database session.

    Returns:
        None
    """
    admin_role_id = session.exec(select(Role.id).where(Role.name == "Admin")).first()
    if admin_role_id:
        all_permission_ids = [
            p.id for p in session.exec(select(Permission)).all() if p.id not in TRUST_SCOPED_PERMISSIONS
        ]
        _grant_permissions(session, admin_role_id, all_permission_ids)
    else:
        logger.debug("Admin role not found. Cannot seed role permissions.")

    researcher_role_id = session.exec(select(Role.id).where(Role.name == "Researcher")).first()
    if researcher_role_id:
        _grant_permissions(session, researcher_role_id, [PermissionRef.CAN_CREATE_PROJECTS.value])
    else:
        logger.debug("Researcher role not found. Cannot seed role permissions.")

    trust_owner_role_id = session.exec(select(Role.id).where(Role.name == "Trust Owner")).first()
    if trust_owner_role_id:
        _grant_permissions(session, trust_owner_role_id, sorted(TRUST_SCOPED_PERMISSIONS))
    else:
        logger.debug("Trust Owner role not found. Cannot seed role permissions.")

    logger.info("Role permissions seeded successfully.")


if __name__ == "__main__":
    with Session(get_engine()) as session:
        seed_role_permissions(session)
        logger.info("Role permissions seeding completed.")
        session.close()
        logger.info("Session closed.")
