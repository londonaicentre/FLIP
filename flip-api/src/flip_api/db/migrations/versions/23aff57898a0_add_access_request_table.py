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

"""add access request table

Revision ID: 23aff57898a0
Revises: 75c871e682a4
Create Date: 2026-07-03 12:07:31.577551

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '23aff57898a0'
down_revision: str | None = '75c871e682a4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        'access_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('reason_for_access', sa.String(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ENROLLED', 'DISMISSED', name='accessrequeststatus'), nullable=False),
        sa.Column('email_notified', sa.Boolean(), nullable=False),
        sa.Column('handled_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_table('access_request')
    # `op.create_table` with a `sa.Enum(name=...)` column auto-creates the native
    # PG type on upgrade, but `op.drop_table` does NOT drop it. Without this a
    # `downgrade base` leaves the type orphaned and the next `upgrade head` fails
    # with "type already exists". `IF EXISTS` keeps the downgrade idempotent.
    op.execute("DROP TYPE IF EXISTS accessrequeststatus")
