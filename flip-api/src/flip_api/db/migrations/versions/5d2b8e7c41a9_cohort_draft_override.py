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

"""cohort draft override

An admin's per-user override of the cohort SQL drafting limits (FLIP#1556), with an append-only audit
of every set and clear.

Revision ID: 5d2b8e7c41a9
Revises: 94570df663ed
Create Date: 2026-11-13 15:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5d2b8e7c41a9'
down_revision: str | None = '94570df663ed'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table('cohort_draft_override',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('monthly_allowance', sa.Integer(), nullable=False),
    sa.Column('monthly_request_cap', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('cohort_draft_override_audit',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=False),
    sa.Column('target_user_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('old_monthly_allowance', sa.Integer(), nullable=True),
    sa.Column('new_monthly_allowance', sa.Integer(), nullable=True),
    sa.Column('old_monthly_request_cap', sa.Integer(), nullable=True),
    sa.Column('new_monthly_request_cap', sa.Integer(), nullable=True),
    sa.Column('old_expires_at', sa.DateTime(), nullable=True),
    sa.Column('new_expires_at', sa.DateTime(), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        op.f('ix_cohort_draft_override_audit_target_user_id'),
        'cohort_draft_override_audit',
        ['target_user_id'],
        unique=False,
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(op.f('ix_cohort_draft_override_audit_target_user_id'), table_name='cohort_draft_override_audit')
    op.drop_table('cohort_draft_override_audit')
    op.drop_table('cohort_draft_override')
