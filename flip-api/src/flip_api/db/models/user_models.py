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

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel

from flip_api.domain.schemas.status import AccessRequestStatus


class Permission(SQLModel, table=True):
    """Permission table."""

    __tablename__ = "permission"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    permission_name: str = Field()
    permission_description: str = Field()

    def __repr__(self) -> str:
        return self.permission_name


class PermissionRef(Enum):
    """Enum for predefined permissions.

    Values are real :class:`UUID` objects, not strings. Consumers should
    pass ``.value`` directly to SQLModel UUID columns or compare against
    UUIDs fetched from the DB — no ``UUID(...)`` wrapping needed.
    """

    CAN_ACCESS_ADMIN_PANEL = UUID("6d1da2d7-e510-488c-9085-8608cd817256")
    CAN_APPROVE_PROJECTS = UUID("e2703e64-0186-4bc1-ac40-ba4ef63255ec")
    CAN_CREATE_PROJECTS = UUID("1140ab45-fefb-4a3b-b350-90dd84f9be4b")
    CAN_DELETE_ANY_PROJECT = UUID("fa2f251f-9d4d-4ba7-8dd1-becd9230dac0")
    CAN_MANAGE_DEPLOYMENTS = UUID("4c9769ed-a939-4c73-b320-139f574368c3")
    CAN_MANAGE_PROJECTS = UUID("fc2f242e-848f-4983-ab14-fd3c5604535d")
    CAN_MANAGE_SITE_BANNER = UUID("5a6c4185-6c90-4b26-bc87-f9436e2042b8")
    CAN_MANAGE_USERS = UUID("f6dbd04e-d1ef-4cb7-84c2-c29fb35cf83b")
    CAN_UNSTAGE_PROJECTS = UUID("a695be07-23c7-448d-a5df-36c3f63ca29d")

    # Trust-scoped permissions — meaningful ONLY when held with a `trust_id`
    # (see TRUST_SCOPED_PERMISSIONS below and `has_trust_permissions`).
    CAN_APPROVE_FOR_TRUST = UUID("3f9c5d21-7a64-4f0e-9b1d-2c8e6a4b7f35")
    CAN_MANAGE_TRUST_GOVERNANCE = UUID("b7e41a68-0d2f-4c95-8e37-5a1b9c6d4e82")
    CAN_MANAGE_TRUST_OWNERS = UUID("d24f8b73-6c19-4a5e-b8d0-7f3e2c9a5146")


# Permissions that are only ever granted against a specific trust. A holder of
# one of these at trust X has authority over trust X and nowhere else.
#
# These are deliberately EXCLUDED from the "Admin gets every permission" grant in
# `db.seed.role_permissions`: hub-wide administration is not trust authority, and
# a blanket grant would hand every hub admin approval rights over every trust's
# data — which is exactly what FLIP#1258 exists to prevent. Enforcement lives in
# `has_trust_permissions`, which ignores global (trust_id IS NULL) grants; this
# set keeps the seeder from quietly creating them in the first place.
TRUST_SCOPED_PERMISSIONS: frozenset[UUID] = frozenset(
    {
        PermissionRef.CAN_APPROVE_FOR_TRUST.value,
        PermissionRef.CAN_MANAGE_TRUST_GOVERNANCE.value,
        PermissionRef.CAN_MANAGE_TRUST_OWNERS.value,
    }
)


class RoleRef(Enum):
    """Enum for predefined roles.

    Values are real :class:`UUID` objects, not strings. See
    :class:`PermissionRef` for the same contract.
    """

    ADMIN = UUID("64d3145b-034c-4328-b637-8eb54313b7c5")
    RESEARCHER = UUID("10b64ed0-bc90-4c01-9cc3-933c704905c1")
    VIEWER = UUID("cdee79c9-a5e1-4b9e-a315-1ec2f3d29efe")
    # Held per-trust: a TRUST_OWNER row always carries a `trust_id`.
    TRUST_OWNER = UUID("8a3d6f14-9b52-4e07-a6c8-1d4f7b2e9053")


class UserRole(SQLModel, table=True):
    """User role mapping table.

    ``user_id`` holds a Cognito ``sub`` UUID. There is intentionally no FK to
    a local users table — Cognito is the source of truth for user identity.

    ``trust_id`` scopes the grant (FLIP#1260):

    * ``NULL`` — a global role (Admin, Researcher, Viewer), platform-wide.
    * set — the role is held *only* at that trust (Trust Owner). Owning two
      trusts is two rows, which needs no special case.

    The two are not interchangeable in either direction. A global grant does not
    satisfy a trust-scoped check (``has_trust_permissions``), and a trust-scoped
    grant does not satisfy a global one (``has_permissions``) — otherwise trust
    authority would leak platform-wide.

    The primary key is a surrogate ``id`` rather than the natural tuple because
    Postgres forbids NULL in a primary-key column, and ``trust_id`` must be
    nullable to express a global role. Uniqueness is enforced instead by two
    partial indexes (declared in ``__table_args__`` and created by the FLIP#1260
    migration): one over ``(user_id, role_id)``
    where ``trust_id IS NULL``, one over ``(user_id, role_id, trust_id)`` where it
    is NOT NULL. A plain UNIQUE constraint would not do: Postgres treats NULLs as
    distinct, so it would let the same global role be granted twice.
    """

    __tablename__ = "user_role"

    # Declared here, not only in the FLIP#1260 migration: the drift guard
    # (tests/integration/test_migrations.py) diffs SQLModel.metadata against the
    # migrated schema, so an index that lives only in a migration reads as drift.
    __table_args__ = (
        Index(
            "uq_user_role_global",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("trust_id IS NULL"),
        ),
        Index(
            "uq_user_role_per_trust",
            "user_id",
            "role_id",
            "trust_id",
            unique=True,
            postgresql_where=text("trust_id IS NOT NULL"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(index=True)
    role_id: UUID = Field(foreign_key="roles.id")
    trust_id: UUID | None = Field(default=None, foreign_key="trust.id", index=True)


class UserProfile(SQLModel, table=True):
    """DB-backed profile data for a Cognito user.

    `name` and `organisation` are operator-supplied strings rendered to other
    users via Vue `{{ }}` interpolation (project card `owner_name`, audit log
    actor labels, etc.). Vue escapes `{{ }}` by default, so the current UI is
    safe. Treat both fields as UNTRUSTED CONTENT — if you ever render them via
    `v-html`, export them to PDF/CSV, or paste them into an email template,
    re-escape at that boundary. The 255-char cap is a length bound, not a
    content filter.
    """

    __tablename__ = "user_profile"

    user_id: UUID = Field(primary_key=True)
    name: str = Field(default="", max_length=255)
    organisation: str = Field(default="", max_length=255)
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


class AccessRequest(SQLModel, table=True):
    """A platform access request from the unauthenticated `/auth/access-request` page.

    Persisted so a request is never lost when the best-effort admin-notification
    email cannot be sent (the email backend must not gate submission — see #699),
    and so administrators can triage requests in-app (enroll / dismiss).

    `email`, `full_name` and `reason_for_access` are operator-supplied and
    UNTRUSTED — treat them like `UserProfile.name` (re-escape at any `v-html`,
    email-template or export boundary; the length caps are bounds, not filters).
    """

    __tablename__ = "access_request"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(max_length=255)
    full_name: str = Field(max_length=255)
    reason_for_access: str = Field()
    status: AccessRequestStatus = Field(default=AccessRequestStatus.PENDING)
    # Whether the best-effort admin-notification email was dispatched. Lets
    # operators find requests still needing a manual follow-up when SES is down
    # (`WHERE email_notified IS false`).
    email_notified: bool = Field(default=False)
    # Cognito sub of the administrator who last enrolled/dismissed the request.
    handled_by_user_id: UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
