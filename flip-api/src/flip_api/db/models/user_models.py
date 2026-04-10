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

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Permission(SQLModel, table=True):
    """Permission table."""

    __tablename__ = "permission"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    permission_name: str = Field()
    permission_description: str = Field()

    def __repr__(self):
        return self.permission_name


class PermissionRef(str, Enum):
    """Enum for predefined permissions."""

    CAN_ACCESS_ADMIN_PANEL = "6d1da2d7-e510-488c-9085-8608cd817256"
    CAN_APPROVE_PROJECTS = "e2703e64-0186-4bc1-ac40-ba4ef63255ec"
    CAN_DELETE_ANY_PROJECT = "fa2f251f-9d4d-4ba7-8dd1-becd9230dac0"
    CAN_MANAGE_DEPLOYMENTS = "4c9769ed-a939-4c73-b320-139f574368c3"
    CAN_MANAGE_PROJECTS = "fc2f242e-848f-4983-ab14-fd3c5604535d"
    CAN_MANAGE_SITE_BANNER = "5a6c4185-6c90-4b26-bc87-f9436e2042b8"
    CAN_MANAGE_USERS = "f6dbd04e-d1ef-4cb7-84c2-c29fb35cf83b"
    CAN_UNSTAGE_PROJECTS = "a695be07-23c7-448d-a5df-36c3f63ca29d"


class RoleRef(str, Enum):
    """Enum for predefined roles."""

    ADMIN = UUID("64d3145b-034c-4328-b637-8eb54313b7c5")
    RESEARCHER = UUID("10b64ed0-bc90-4c01-9cc3-933c704905c1")


class UserRole(SQLModel, table=True):
    """User role mapping table."""

    __tablename__ = "user_role"

    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    role_id: UUID = Field(foreign_key="roles.id", primary_key=True)


class User(SQLModel, table=True):
    """User table."""

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Role(SQLModel, table=True):
    """Role table."""

    __tablename__ = "roles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(unique=True, description="Name of the role")
    description: str = Field(..., description="Description of the role")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RolePermission(SQLModel, table=True):
    """Role permission mapping table."""

    __tablename__ = "role_permission"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    role_id: UUID = Field(foreign_key="roles.id", ondelete="CASCADE")
    permission_id: UUID = Field(foreign_key="permission.id", ondelete="CASCADE")


class UsersAudit(SQLModel, table=True):
    """Audit table for user changes."""

    __tablename__ = "users_audit"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    action: str
    user_id: UUID
    modified_by_user_id: UUID
    timestamp: datetime = Field(default_factory=datetime.utcnow)
