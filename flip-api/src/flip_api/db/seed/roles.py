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


from typing import TypedDict
from uuid import UUID

from sqlmodel import Session, select

from flip_api.db.models.user_models import Role, RoleRef


class RoleSeed(TypedDict):
    """A predefined role to seed; ``id`` is the stable :class:`RoleRef` identity."""

    id: UUID
    name: str
    description: str


CURRENT_ROLES: list[RoleSeed] = [
    {
        "id": RoleRef.RESEARCHER.value,
        "name": "Researcher",
        "description": "A researcher role, used as the default role with minimal permissions.",
    },
    {
        "id": RoleRef.ADMIN.value,
        "name": "Admin",
        "description": "A role for administrators.",
    },
    {
        "id": RoleRef.VIEWER.value,
        "name": "Viewer",
        "description": "Read-only access to assigned projects. Cannot create, edit, or delete resources.",
    },
]


def seed_roles(session: Session) -> list[str]:
    """Seed roles into the database, idempotently.

    Idempotency is keyed on the role ``id`` (the stable :class:`RoleRef`), not the
    name. A role may be renamed across releases while keeping its id — e.g. the
    ``observer`` → ``viewer`` rename — so matching on the (mutable) name would miss
    the existing row and collide on the primary key when re-seeding a live database.
    An existing role has its name/description refreshed in place so renames apply.

    Args:
        session (Session): The SQLModel session used for reads and writes.

    Returns:
        list[str]: All role names present after seeding.
    """
    for role_data in CURRENT_ROLES:
        existing_role = session.get(Role, role_data["id"])
        if existing_role:
            # Refresh in place so a rename / description change is applied without
            # re-inserting the already-present primary key.
            existing_role.name = role_data["name"]
            existing_role.description = role_data["description"]
        else:
            session.add(Role(**role_data))

    session.commit()

    # Return list of role names
    roles = session.exec(select(Role.name)).all()
    return list(roles)
