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

"""fl_logs and fl_job model indexes

Revision ID: b4e1d7c09a52
Revises: a6174749cbb5
Create Date: 2026-11-13 18:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4e1d7c09a52'
down_revision: str | None = 'a6174749cbb5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # The Models list orders by each model's latest log and job activity (FLIP#1934); Postgres does not
    # index foreign keys, so without these every list request scans both tables once per model.
    op.create_index('ix_fl_logs_model_id_log_date', 'fl_logs', ['model_id', 'log_date'], unique=False)
    op.create_index(op.f('ix_fl_job_model_id'), 'fl_job', ['model_id'], unique=False)


def downgrade() -> None:
    """Revert this revision."""
    op.drop_index(op.f('ix_fl_job_model_id'), table_name='fl_job')
    op.drop_index('ix_fl_logs_model_id_log_date', table_name='fl_logs')
