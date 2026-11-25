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

"""model derived from

Adds ``model.derived_from_model_id``: the model of the same project a model was built from (FLIP#2060), e.g. the
fingerprint job whose result planned a training job. Nullable, so every existing model carries on unchanged.

Revision ID: e5b2a7c4d913
Revises: c3e1d7a9b4f2
Create Date: 2026-11-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b2a7c4d913"
down_revision: str | None = "c3e1d7a9b4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column("model", sa.Column("derived_from_model_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("model_derived_from_model_id_fkey", "model", "model", ["derived_from_model_id"], ["id"])


def downgrade() -> None:
    """Revert this revision."""
    op.drop_constraint("model_derived_from_model_id_fkey", "model", type_="foreignkey")
    op.drop_column("model", "derived_from_model_id")
