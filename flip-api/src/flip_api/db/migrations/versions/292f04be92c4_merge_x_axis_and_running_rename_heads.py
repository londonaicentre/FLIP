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

"""merge x-axis and RUNNING-rename heads

No-op merge point: this branch's ee61ab95ef2b (fl_metrics x_label/x_value,
#148) descends from 8c4f2b9d31e7, while develop's 01af591a5cfc merge point
joined 8c4f2b9d31e7 with the RUNNING rename (8c41d0f2ab5e) — so merging
develop in leaves two heads again. The branches touch disjoint objects
(fl_metrics columns vs the modelstatus/modelauditaction enums), so they
commute; a database at either head — prod is at ee61ab95ef2b — upgrades
cleanly through this merge, running whichever branch it is missing.

Revision ID: 292f04be92c4
Revises: 01af591a5cfc, ee61ab95ef2b
Create Date: 2026-07-21 15:30:00.000000

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '292f04be92c4'
down_revision: str | Sequence[str] | None = ('01af591a5cfc', 'ee61ab95ef2b')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision (no-op merge point)."""


def downgrade() -> None:
    """Revert this revision (no-op merge point)."""
