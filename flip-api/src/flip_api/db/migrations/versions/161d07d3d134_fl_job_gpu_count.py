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

"""fl_job GPU count override

Revision ID: 161d07d3d134
Revises: b3f1c857a001
Create Date: 2026-10-12 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '161d07d3d134'
down_revision: str | None = 'b3f1c857a001'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # The researcher's GPU override for a run, per trust (FLIP#70). Nullable with no backfill: NULL means
    # no override, so every existing row keeps meaning what it meant (the job's own request or the default).
    # Replaces an unreleased revision, f5ad2a562fad, that also stored memory per GPU. A database stamped with it
    # fails `alembic upgrade` on the unknown revision rather than silently keeping a column the code dropped.
    op.add_column('fl_job', sa.Column('num_gpus', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('fl_job', 'num_gpus')
