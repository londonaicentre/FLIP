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

from sqlmodel import Session, select

from flip_api.db.models.user_models import Permission, PermissionRef


def seed_permissions(session: Session) -> list[str]:
    """Seed permissions into the database, idempotently.

    Idempotency is keyed on the permission ``id`` (the stable :class:`PermissionRef`),
    not the name — matching ``seed_roles``: a permission may be renamed while keeping
    its id, so a name-keyed check would miss the existing row and collide on the
    primary key when re-seeding a live database. An existing permission has its
    name/description refreshed in place so renames apply.

    Args:
        session (Session): The SQLModel session used for reads and writes.

    Returns:
        list[str]: All permission names present after seeding.
    """
    for perm_data in PermissionRef:
        existing_permission = session.get(Permission, perm_data.value)
        if existing_permission:
            # Refresh in place so a rename is applied without re-inserting the
            # already-present primary key.
            existing_permission.permission_name = perm_data.name
            existing_permission.permission_description = perm_data.name
        else:
            session.add(
                Permission(
                    id=perm_data.value,
                    permission_name=perm_data.name,
                    permission_description=perm_data.name,
                )
            )

    session.commit()

    # Return list of permission names
    permissions = session.exec(select(Permission.permission_name)).all()
    return list(permissions)
