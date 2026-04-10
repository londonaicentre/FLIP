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

from sqlmodel import Session, select

from flip_api.db.models.user_models import Role, RoleRef

CURRENT_ROLES = [
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
]


def seed_roles(session: Session) -> List[str]:
    """Seed roles into the database."""
    for role_data in CURRENT_ROLES:
        # Check if role exists
        statement = select(Role).where(Role.name == role_data["name"])
        existing_role = session.exec(statement).first()

        if existing_role:
            continue  # Skip if role already exists
        else:
            # Create new role
            new_role = Role(**role_data)
            session.add(new_role)

    session.commit()

    # Return list of role names
    roles = session.exec(select(Role.name)).all()
    return list(roles)
