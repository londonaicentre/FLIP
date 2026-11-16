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

"""fl job credential hash

Revision ID: a6174749cbb5
Revises: 9d4b7a1c5e20
Create Date: 2026-11-11 14:01:45.882576

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a6174749cbb5'
down_revision: str | None = '9d4b7a1c5e20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # Hash of the per-job credential the job's FL server presents on its callbacks. Nullable with no backfill:
    # a job dispatched before this revision has none, and its callbacks are refused.
    op.add_column('fl_job', sa.Column('credential_hash', sa.String(), nullable=True))


def downgrade() -> None:
    """Revert this revision."""
    op.drop_column('fl_job', 'credential_hash')
