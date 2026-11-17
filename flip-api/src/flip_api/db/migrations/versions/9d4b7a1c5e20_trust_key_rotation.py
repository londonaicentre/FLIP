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

"""trust key rotation

Adds ROTATED to ``trustauditaction`` so the deploy CLI can record that a trust's credentials were reissued without
delete and re-register. Re-registration was the only way to change a key, and it either hits the duplicate-name guard
or, with delete first, drops the trust's tasks, metrics and FL kit slot assignment.

Revision ID: 9d4b7a1c5e20
Revises: 161d07d3d134
Create Date: 2026-10-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d4b7a1c5e20"
down_revision: str | None = "161d07d3d134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # Autocommit block: the value cannot be added and used in the same transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS 'ROTATED'")


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so ROTATED stays in ``trustauditaction``; the rows that use it are deleted,
    as older code cannot read them.
    """
    op.execute("DELETE FROM trusts_audit WHERE action = 'ROTATED'")
