# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""staged trust key rotation

Adds ``trust.pending_api_key_hash`` and ``trust.pending_api_key_expires_at`` so a rotated key is staged next to the
live one until the trust first uses it (FLIP#1714), and the KEY_PROMOTED / KEY_EXPIRED audit actions that close a
staged rotation. Both columns are nullable and default to NULL, so every existing trust carries on unchanged.

Revision ID: c3e1d7a9b4f2
Revises: a6174749cbb5
Create Date: 2026-11-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e1d7a9b4f2"
down_revision: str | None = "a6174749cbb5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("trust", sa.Column("pending_api_key_hash", sa.String(), nullable=True))
    op.add_column("trust", sa.Column("pending_api_key_expires_at", sa.DateTime(), nullable=True))
    # Autocommit block: an enum value cannot be added and used in the same transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'KEY_PROMOTED'")
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'KEY_EXPIRED'")


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so the two actions stay in ``trustauditaction``; the rows that use them are
    deleted, as older code cannot read them. A pending key is dropped with its column, so a trust mid-rotation keeps
    its live key.
    """
    op.execute("DELETE FROM trusts_audit WHERE action IN ('KEY_PROMOTED', 'KEY_EXPIRED')")
    op.drop_column("trust", "pending_api_key_expires_at")
    op.drop_column("trust", "pending_api_key_hash")
