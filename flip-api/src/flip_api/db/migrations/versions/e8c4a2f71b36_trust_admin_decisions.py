# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""trust admin decisions

Records who decided each trust — the hub admin or the trust's own Trust Admin — and who a Trust Admin audit row
is about (FLIP#1258), and retires the never-enforced CAN_MANAGE_TRUST_OWNERS permission. The role row itself is
renamed Trust Owner → Trust Admin by the seeder, which refreshes role names by id.

Revision ID: e8c4a2f71b36
Revises: c3a7f1eb9402
Create Date: 2026-09-28 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e8c4a2f71b36"  # pragma: allowlist secret
down_revision: str | None = "c3a7f1eb9402"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created and dropped explicitly below: add_column never creates a native enum type on its own.
decision_maker = postgresql.ENUM("HUB", "SITE", name="decisionmaker", create_type=False)
# The retired PermissionRef.CAN_MANAGE_TRUST_OWNERS, spelled out because the enum member is gone.
_CAN_MANAGE_TRUST_OWNERS = "d24f8b73-6c19-4a5e-b8d0-7f3e2c9a5146"  # pragma: allowlist secret


def upgrade() -> None:
    """Apply this revision."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'ADMIN_ADDED'")
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'ADMIN_REMOVED'")
    op.add_column("trusts_audit", sa.Column("subject_user_id", sa.Uuid(), nullable=True))

    op.execute("CREATE TYPE decisionmaker AS ENUM ('HUB', 'SITE')")
    op.add_column("project_trust_intersect", sa.Column("decided_as", decision_maker, nullable=True))
    # Every decision before this revision was the hub's: nobody could decide for a single trust.
    op.execute("UPDATE project_trust_intersect SET decided_as = 'HUB' WHERE status <> 'PENDING'")
    # From here a pending trust on an approved project may still approve and join its models. One left pending
    # before (left out when the others were approved) is closed instead: DECLINED with no decider, date or
    # decided_as, the mark that nobody declined it. Runs after the HUB backfill, which it must not reach.
    op.execute(
        "UPDATE project_trust_intersect i SET status = 'DECLINED' FROM projects p "
        "WHERE p.id = i.project_id AND p.status = 'APPROVED' AND i.status = 'PENDING'"
    )

    # Its role_permission rows go with it (ondelete=CASCADE).
    op.execute(f"DELETE FROM permission WHERE id = '{_CAN_MANAGE_TRUST_OWNERS}'")


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so ADMIN_ADDED / ADMIN_REMOVED stay in ``trustauditaction``; the rows that
    use them are deleted, as older code cannot read them. The older seeder re-creates CAN_MANAGE_TRUST_OWNERS.
    The trusts closed at upgrade — the only declines with no decider, date or decided_as — reopen as PENDING.
    """
    op.execute(
        "UPDATE project_trust_intersect i SET status = 'PENDING' FROM projects p "
        "WHERE p.id = i.project_id AND p.status = 'APPROVED' AND i.status = 'DECLINED' "
        "AND i.decided_by IS NULL AND i.decided_at IS NULL AND i.decided_as IS NULL"
    )
    op.drop_column("project_trust_intersect", "decided_as")
    op.execute("DROP TYPE IF EXISTS decisionmaker")
    op.execute("DELETE FROM trusts_audit WHERE action IN ('ADMIN_ADDED', 'ADMIN_REMOVED')")
    op.drop_column("trusts_audit", "subject_user_id")
