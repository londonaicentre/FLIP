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

"""add has_imaging to projects

Revision ID: 40f7934c6419
Revises: 46edb903e4d1
Create Date: 2026-09-03 09:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '40f7934c6419'  # pragma: allowlist secret
down_revision: str | None = '46edb903e4d1'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision.

    FLIP#1071: creation-time ``has_imaging`` flag. Every existing project is an imaging project, so the
    NOT NULL column is backfilled ``true`` at the DB level via ``server_default``; the model keeps a plain
    Python default (the drift guard does not compare server defaults, same as ``fl_metrics.x_label``).
    """
    op.add_column(
        'projects',
        sa.Column('has_imaging', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('projects', 'has_imaging')
