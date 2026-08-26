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

"""persist_cohort task type and cohort_snapshot_status

Approved-cohort snapshots (FLIP#857): adds the PERSIST_COHORT member to the native
``tasktype`` Postgres enum (the approval-time task that makes each trust freeze its
cohort) and the ``cohort_snapshot_status`` table — the hub's per-(project, trust)
audit record of what was frozen (aggregates only; the row-level cohort never leaves
the trust). ADD VALUE cannot run inside the migration transaction, hence the
autocommit block; it is appended last so migrated databases keep the same enum
order as fresh ones.

Revision ID: b3f1c857a001
Revises: 46edb903e4d1
Create Date: 2026-08-26 18:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3f1c857a001'
down_revision: str | None = '46edb903e4d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'PERSIST_COHORT'")
    op.create_table(
        'cohort_snapshot_status',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=True),
        sa.Column('trust_id', sa.Uuid(), nullable=True),
        sa.Column('query_id', sa.Uuid(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('approved_record_count', sa.Integer(), nullable=True),
        sa.Column('has_accessions', sa.Boolean(), nullable=False),
        sa.Column('query_hash', sa.String(), nullable=True),
        sa.Column('snapshot_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['trust_id'], ['trust.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so PERSIST_COHORT stays in the type on
    downgrade — harmless, as pre-#857 code never writes it.
    """
    op.drop_table('cohort_snapshot_status')
