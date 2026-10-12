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

"""record per-trust project decisions

FLIP#1318: ``project_trust_intersect.approved`` (bool) becomes ``status`` (PENDING / APPROVED /
DECLINED) plus the deciding user, and ``approved_at`` becomes ``decided_at``. A pre-existing
``approved = false`` maps to PENDING, never DECLINED: it covered both "never looked at" and "left
out when the others were approved", and neither may be recorded as a refusal. Historical rows have
no recorded approver, so ``decided_by`` stays NULL.

``projects_audit`` gains ``trust_id`` and the APPROVE_TRUST / DECLINE_TRUST actions, so each
decision is audited against its trust. The new enum values go in an autocommit block because a value
added inside a transaction cannot be used until that transaction commits, and Alembic runs every
pending revision in one; they are appended, matching their order in ``ProjectAuditAction``.

Revision ID: b7e3a1c95d20
Revises: 40f7934c6419
Create Date: 2026-09-28 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7e3a1c95d20'  # pragma: allowlist secret
down_revision: str | None = '40f7934c6419'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created and dropped explicitly below: add_column never creates a native enum type on its own.
trust_approval_status = postgresql.ENUM(
    'PENDING', 'APPROVED', 'DECLINED', name='trustapprovalstatus', create_type=False
)


def upgrade() -> None:
    """Apply this revision."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE projectauditaction ADD VALUE IF NOT EXISTS 'APPROVE_TRUST'")
        op.execute("ALTER TYPE projectauditaction ADD VALUE IF NOT EXISTS 'DECLINE_TRUST'")
    op.add_column('projects_audit', sa.Column('trust_id', sa.Uuid(), nullable=True))

    op.execute("CREATE TYPE trustapprovalstatus AS ENUM ('PENDING', 'APPROVED', 'DECLINED')")
    op.add_column('project_trust_intersect', sa.Column('status', trust_approval_status, nullable=True))
    op.add_column('project_trust_intersect', sa.Column('decided_by', sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE project_trust_intersect "
        "SET status = CASE WHEN approved THEN 'APPROVED'::trustapprovalstatus ELSE 'PENDING'::trustapprovalstatus END"
    )
    op.alter_column('project_trust_intersect', 'status', nullable=False)
    op.alter_column('project_trust_intersect', 'approved_at', new_column_name='decided_at')
    op.drop_column('project_trust_intersect', 'approved')


def downgrade() -> None:
    """Revert this revision.

    DECLINED folds back into ``approved = false`` with no date, the only "not approved" the old schema
    has. Postgres cannot drop an enum value, so APPROVE_TRUST / DECLINE_TRUST stay in
    ``projectauditaction``; the audit rows that use them are deleted, as pre-#1318 code cannot read them.
    """
    op.add_column('project_trust_intersect', sa.Column('approved', sa.Boolean(), nullable=True))
    op.execute("UPDATE project_trust_intersect SET approved = (status = 'APPROVED')")
    op.execute("UPDATE project_trust_intersect SET decided_at = NULL WHERE status <> 'APPROVED'")
    op.alter_column('project_trust_intersect', 'approved', nullable=False)
    op.alter_column('project_trust_intersect', 'decided_at', new_column_name='approved_at')
    op.drop_column('project_trust_intersect', 'decided_by')
    op.drop_column('project_trust_intersect', 'status')
    op.execute("DROP TYPE IF EXISTS trustapprovalstatus")

    op.execute("DELETE FROM projects_audit WHERE action IN ('APPROVE_TRUST', 'DECLINE_TRUST')")
    op.drop_column('projects_audit', 'trust_id')
