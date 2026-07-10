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

"""add x_label and x_value to fl_metrics

Revision ID: ee61ab95ef2b
Revises: 23aff57898a0
Create Date: 2026-07-10 18:28:41.397629

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ee61ab95ef2b'
down_revision: str | None = '23aff57898a0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # A metric's plot coordinate becomes the (x_label, x_value) pair, while global_round stays as
    # provenance (the true FL round it was reported in) — see FLIP#148.
    #
    # x_label names the x-axis a metric is plotted against. A NOT NULL column added to a table with
    # existing rows needs a default to backfill them, so ship a server_default of the historical
    # label ("Global Round"). The app always supplies x_label on insert (SQLModel field default), so
    # the DB default is only a backfill + safety net.
    op.add_column(
        'fl_metrics',
        sa.Column('x_label', sa.String(), nullable=False, server_default='Global Round'),
    )
    # x_value is the x-coordinate. Existing rows were plotted at their global_round, so backfill from
    # that column (a per-row value — can't be a constant server_default) before tightening to NOT
    # NULL. The ingest schema always supplies x_value on insert, so no server_default is needed.
    op.add_column('fl_metrics', sa.Column('x_value', sa.Float(), nullable=True))
    op.execute('UPDATE fl_metrics SET x_value = global_round')
    op.alter_column('fl_metrics', 'x_value', existing_type=sa.Float(), nullable=False)


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('fl_metrics', 'x_value')
    op.drop_column('fl_metrics', 'x_label')
