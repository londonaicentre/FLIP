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

"""trust auto-upgrade

The Trust Admin's automatic-upgrade switch and maintenance window, the lease a trust's host agent holds while it
upgrades, and the outcome of the last attempt. Every existing trust starts OFF (grandfathered): the column defaults
to false and nothing is backfilled, so no live site is upgraded until its own admin opts in.

Revision ID: a91d3c5e7b20
Revises: 161d07d3d134
Create Date: 2026-10-23 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a91d3c5e7b20"  # pragma: allowlist secret
down_revision: str | None = "161d07d3d134"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: list[sa.Column] = [
    sa.Column("auto_upgrade_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    sa.Column("auto_upgrade_window_start", sa.String(), nullable=True),
    sa.Column("auto_upgrade_window_end", sa.String(), nullable=True),
    sa.Column("auto_upgrade_timezone", sa.String(), nullable=True),
    sa.Column("upgrade_lease_expires_at", sa.DateTime(), nullable=True),
    sa.Column("upgrade_lease_release", sa.String(), nullable=True),
    sa.Column("last_upgrade_state", sa.String(), nullable=True),
    sa.Column("last_upgrade_release", sa.String(), nullable=True),
    sa.Column("last_upgrade_at", sa.DateTime(), nullable=True),
    sa.Column("last_upgrade_detail", sa.Text(), nullable=True),
    sa.Column("upgrade_agent_seen_at", sa.DateTime(), nullable=True),
    sa.Column("upgrade_blocker", sa.String(), nullable=True),
    sa.Column("upgrade_blocker_at", sa.DateTime(), nullable=True),
    sa.Column("upgrade_pending_since", sa.DateTime(), nullable=True),
]


def upgrade() -> None:
    """Apply this revision."""
    for column in _COLUMNS:
        op.add_column("trust", column.copy())


def downgrade() -> None:
    """Revert this revision."""
    for column in reversed(_COLUMNS):
        op.drop_column("trust", column.name)
