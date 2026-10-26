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

"""stage_job_data and unstage_job_data task types

Per-job cohort data (FLIP#1678): adds the STAGE_JOB_DATA and UNSTAGE_JOB_DATA members to the native ``tasktype``
Postgres enum. ADD VALUE cannot run inside the migration transaction, hence the autocommit block; the values are
appended last so migrated databases keep the same enum order as fresh ones.

Revision ID: 4c2d1678a0b1
Revises: 161d07d3d134
Create Date: 2026-10-26 23:50:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4c2d1678a0b1'  # pragma: allowlist secret
down_revision: str | None = '161d07d3d134'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'STAGE_JOB_DATA'")
        op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'UNSTAGE_JOB_DATA'")


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so both stay in the type on downgrade (as PERSIST_COHORT does); nothing
    references them once the code that queues them is gone.
    """
