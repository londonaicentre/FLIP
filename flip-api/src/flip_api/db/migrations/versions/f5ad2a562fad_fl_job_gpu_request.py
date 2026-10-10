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

"""fl_job GPU request override

Revision ID: f5ad2a562fad
Revises: e8c4a2f71b36
Create Date: 2026-10-10 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5ad2a562fad'
down_revision: str | None = 'e8c4a2f71b36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # The researcher's GPU override for a run, per trust (FLIP#70). Nullable with no backfill: NULL means
    # no override, so every existing row keeps meaning what it meant (the job's own request or the default).
    op.add_column('fl_job', sa.Column('num_gpus', sa.Integer(), nullable=True))
    op.add_column('fl_job', sa.Column('mem_per_gpu_gib', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('fl_job', 'mem_per_gpu_gib')
    op.drop_column('fl_job', 'num_gpus')
