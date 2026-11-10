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

"""trust kit download

Records a trust's kit being downloaded from the UI (FLIP#1852): KEYS_ISSUED and KIT_DOWNLOADED in
``trustauditaction``, and a ``detail`` column on ``trusts_audit`` for what a download carried. The new
CAN_MANAGE_TRUST_KIT permission needs no revision; the seeder inserts it and grants it to Trust Admin.

Revision ID: a4c8e1f52d07
Revises: 9d4b7a1c5e20
Create Date: 2026-11-10 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4c8e1f52d07"  # pragma: allowlist secret
down_revision: str | None = "9d4b7a1c5e20"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # Autocommit block: a value cannot be added and used in the same transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'KEYS_ISSUED'")
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'KIT_DOWNLOADED'")
    op.add_column("trusts_audit", sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so the two stay in ``trustauditaction``; the rows that use them are
    deleted, as older code cannot read them. A trust whose keys were never issued keeps its NULL
    ``api_key_hash``, which older code already treats as a trust that cannot authenticate.
    """
    op.execute("DELETE FROM trusts_audit WHERE action IN ('KEYS_ISSUED', 'KIT_DOWNLOADED')")
    op.drop_column("trusts_audit", "detail")
