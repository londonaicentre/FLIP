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

"""sync develop schema changes

Brings the schema up to date with model changes that landed on develop after the
baseline revision was captured (they had only ever been applied via the old
SQLModel.metadata.create_all):

* ``fl_nets.fl_backend`` — new non-null ``flbackend`` enum column (NVFLARE/FLOWER);
  the DB persists the member *name* (the SQLAlchemy Enum default).
* ``modelstatus`` — new value ``RESULTS_UPLOAD_FAILED`` (appended last). Added via
  ``ALTER TYPE ... ADD VALUE`` inside ``autocommit_block()`` (it cannot run inside a
  transaction).
* ``query_stats.query_id`` — now NOT NULL + UNIQUE (issue #579: one aggregate row
  per query, NOT NULL so Postgres's distinct-NULL rule can't bypass the constraint).

Revision ID: e3b1a7c4d2f8
Revises: 75c871e682a4
Create Date: 2026-06-22 18:45:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3b1a7c4d2f8"
down_revision: str | None = "75c871e682a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Native PG enum for the FL backend. create_type=False so we own creation/removal
# explicitly (op.add_column would not create it, and a downgrade must drop it).
fl_backend_enum = postgresql.ENUM("NVFLARE", "FLOWER", name="flbackend", create_type=False)


def upgrade() -> None:
    """Apply this revision."""
    # New enum value first: ALTER TYPE ... ADD VALUE cannot run inside a transaction,
    # so it needs its own autocommit block. IF NOT EXISTS keeps re-runs idempotent.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelstatus ADD VALUE IF NOT EXISTS 'RESULTS_UPLOAD_FAILED'")

    # fl_nets.fl_backend. fl_nets is empty when migrations run (seeding happens after,
    # in the entrypoint), so a NOT NULL column with no server_default is safe.
    fl_backend_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("fl_nets", sa.Column("fl_backend", fl_backend_enum, nullable=False))

    # query_stats.query_id: NOT NULL + UNIQUE (one aggregate row per query, #579).
    op.alter_column("query_stats", "query_id", existing_type=sa.Uuid(), nullable=False)
    op.create_unique_constraint("query_stats_query_id_key", "query_stats", ["query_id"])


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint("query_stats_query_id_key", "query_stats", type_="unique")
    op.alter_column("query_stats", "query_id", existing_type=sa.Uuid(), nullable=True)

    op.drop_column("fl_nets", "fl_backend")
    fl_backend_enum.drop(op.get_bind(), checkfirst=True)

    # modelstatus 'RESULTS_UPLOAD_FAILED' is intentionally left in place: PostgreSQL
    # cannot drop a single enum value. A full `downgrade base` drops the modelstatus
    # type entirely in the baseline downgrade, so the round-trip stays clean.
