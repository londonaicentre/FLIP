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

"""merge trust services health and bandit findings heads

No-op merge point: this branch's ffacf260ebc8 (trust.services_health /
services_health_at, #901) and develop's 6b786e4503f8 (uploaded_files
.bandit_findings, #877) both fork from 2a462371f3c5, so merging develop in
leaves two heads. They add columns to disjoint tables, so they commute; a
database at either head — or at their shared parent, where stag sits —
upgrades cleanly through this merge, running whichever branch it is missing.

Revision ID: 46edb903e4d1
Revises: 6b786e4503f8, ffacf260ebc8
Create Date: 2026-08-12 15:44:12.035813

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '46edb903e4d1'
down_revision: str | Sequence[str] | None = ('6b786e4503f8', 'ffacf260ebc8')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision (no-op merge point)."""
    pass


def downgrade() -> None:
    """Revert this revision (no-op merge point)."""
    pass
