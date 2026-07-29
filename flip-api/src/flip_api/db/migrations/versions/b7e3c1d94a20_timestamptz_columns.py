# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Make all timestamp columns timezone-aware

Every ``TIMESTAMP WITHOUT TIME ZONE`` column becomes ``TIMESTAMP WITH TIME ZONE``
so values are aware end to end — written aware by ``utc_now()``, read back aware —
instead of the previous mix of naive and aware values that forced defensive
``tzinfo is None`` normalisation at read sites.

Existing values were written as UTC (either by the deprecated ``datetime.utcnow()``
or by ``datetime.now(timezone.utc)``, which Postgres stored as its UTC literal), so
the conversion states ``AT TIME ZONE 'UTC'`` explicitly rather than relying on the
server's ``TimeZone`` setting — otherwise a non-UTC server would shift every
timestamp during the ALTER.

Revision ID: b7e3c1d94a20
Revises: 292f04be92c4
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3c1d94a20"
down_revision: str | Sequence[str] | None = "292f04be92c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Every ``(table, column)`` holding a timestamp. Mirrors the ``UTC_DATETIME``
#: columns in ``db/models/{main,user}_models.py``; the integration drift guard
#: (``tests/integration/test_migrations.py``) fails if the two diverge.
TIMESTAMP_COLUMNS: list[tuple[str, str]] = [
    ("fl_job", "created"),
    ("fl_job", "started"),
    ("fl_job", "completed"),
    ("fl_metrics", "timestamp"),
    ("fl_logs", "log_date"),
    ("model", "creation_timestamp"),
    ("models_audit", "audit_date"),
    ("project_trust_intersect", "approved_at"),
    ("projects", "creation_timestamp"),
    ("projects_audit", "audit_date"),
    ("queries", "created"),
    ("query_stats", "stats_received"),
    ("trust", "last_heartbeat"),
    ("trust", "created_at"),
    ("trusts_audit", "audit_date"),
    ("fl_kit_slot", "assigned_at"),
    ("trust_task", "created_at"),
    ("trust_task", "updated_at"),
    ("xnat_project_status", "last_reimport"),
    ("user_profile", "created_at"),
    ("user_profile", "updated_at"),
    ("roles", "created_at"),
    ("roles", "updated_at"),
    ("users_audit", "timestamp"),
    ("access_request", "created_at"),
    ("access_request", "updated_at"),
]


def upgrade() -> None:
    """Convert every timestamp column to ``TIMESTAMP WITH TIME ZONE``."""
    for table, column in TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            # Stored values are UTC; say so explicitly so the result does not depend
            # on the server's TimeZone setting.
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Convert back to naive ``TIMESTAMP``, normalising to UTC on the way out."""
    for table, column in TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
