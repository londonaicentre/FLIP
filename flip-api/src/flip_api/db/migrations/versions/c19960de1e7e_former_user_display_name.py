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

"""former user display name

Revision ID: c19960de1e7e
Revises: a6174749cbb5
Create Date: 2026-11-19 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c19960de1e7e'
down_revision: str | None = 'a6174749cbb5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # A deleted user's profile stays, marked, so history keeps their name (FLIP#1996).
    op.add_column('user_profile', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    op.add_column('users_audit', sa.Column('user_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('users_audit', 'user_name')
    op.drop_column('user_profile', 'deleted_at')
