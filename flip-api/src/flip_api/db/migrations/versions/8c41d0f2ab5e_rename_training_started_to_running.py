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

"""rename TRAINING_STARTED to RUNNING

Renames the TRAINING_STARTED member of the native ``modelstatus`` and
``modelauditaction`` Postgres enums to RUNNING (#782): the status is
job-type-neutral so evaluation jobs can report it too. RENAME VALUE keeps the
member's position and rewrites existing rows in place, and (unlike ADD VALUE)
runs fine inside the migration transaction.

Revision ID: 8c41d0f2ab5e
Revises: 23aff57898a0
Create Date: 2026-07-14 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8c41d0f2ab5e'
down_revision: str | None = '23aff57898a0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.execute("ALTER TYPE modelstatus RENAME VALUE 'TRAINING_STARTED' TO 'RUNNING'")
    op.execute("ALTER TYPE modelauditaction RENAME VALUE 'TRAINING_STARTED' TO 'RUNNING'")


def downgrade() -> None:
    """Revert this revision."""
    op.execute("ALTER TYPE modelstatus RENAME VALUE 'RUNNING' TO 'TRAINING_STARTED'")
    op.execute("ALTER TYPE modelauditaction RENAME VALUE 'RUNNING' TO 'TRAINING_STARTED'")
