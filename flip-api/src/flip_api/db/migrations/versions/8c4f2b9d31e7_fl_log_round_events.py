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

"""fl_logs typed round events

Adds the typed-event columns backing the RoundProgress card and round-aware
Live activity messages: the FL layer reports facts (event_type, global_round,
details) and the hub composes display text at serve time. event_type is plain
VARCHAR (validated by the FLLogEvent StrEnum at the API layer), deliberately
not a native PG enum, so extending the event vocabulary never needs an
ALTER TYPE migration. log becomes nullable because typed event rows carry no
stored text.

Revision ID: 8c4f2b9d31e7
Revises: 23aff57898a0
Create Date: 2026-07-09 17:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = '8c4f2b9d31e7'
down_revision: str | None = '23aff57898a0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column('fl_logs', sa.Column('event_type', sa.String(), nullable=True))
    op.add_column('fl_logs', sa.Column('global_round', sa.Integer(), nullable=True))
    op.add_column('fl_logs', sa.Column('details', JSONB(), nullable=True))
    op.alter_column('fl_logs', 'log', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Revert this revision."""
    # Event rows have no stored text; give them a placeholder so the NOT NULL
    # constraint can be restored without failing on existing data.
    op.execute("UPDATE fl_logs SET log = COALESCE(log, event_type, '') WHERE log IS NULL")
    op.alter_column('fl_logs', 'log', existing_type=sa.String(), nullable=False)
    op.drop_column('fl_logs', 'details')
    op.drop_column('fl_logs', 'global_round')
    op.drop_column('fl_logs', 'event_type')
