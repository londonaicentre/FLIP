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

"""add INFECTED file status and uploaded_files.updated_at

Malware-scan groundwork (#52): adds the INFECTED member to the native
``fileuploadstatus`` Postgres enum (scan verdict — the uploaded object held
dangerous content and was deleted) and ``uploaded_files.updated_at`` (the
scan sweep's staleness guard). ADD VALUE cannot run inside the migration
transaction, hence the autocommit block; it is appended last so migrated
databases keep the same enum order as fresh ones.

Revision ID: 2a462371f3c5
Revises: 292f04be92c4
Create Date: 2026-07-29 16:48:01.810287

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2a462371f3c5'
down_revision: str | None = '292f04be92c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fileuploadstatus ADD VALUE IF NOT EXISTS 'INFECTED'")
    op.add_column('uploaded_files', sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Revert this revision.

    Postgres cannot drop an enum value, so INFECTED stays in the type on
    downgrade — harmless, as pre-#52 code never writes it.
    """
    op.drop_column('uploaded_files', 'updated_at')
