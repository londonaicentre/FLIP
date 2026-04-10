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
from uuid import UUID

from sqlmodel import Session, select

from flip_api.db.models.user_models import Permission, PermissionRef


def seed_permissions(session: Session) -> List[str]:
    """Seed permissions into the database."""
    for perm_data in PermissionRef:
        # Check if permission exists
        statement = select(Permission).where(Permission.permission_name == perm_data.name)
        existing_permission = session.exec(statement).first()

        if not existing_permission:
            # Create new permission
            new_permission = Permission(
                id=UUID(perm_data.value), permission_name=perm_data.name, permission_description=perm_data.name
            )
            session.add(new_permission)

    session.commit()

    # Return list of permission names
    permissions = session.exec(select(Permission.permission_name)).all()
    return list(permissions)
