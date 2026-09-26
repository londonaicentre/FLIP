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

"""trust-scoped user roles

Adds ``user_role.trust_id`` so a role can be held at a single trust rather than
platform-wide (FLIP#1260), and swaps the composite primary key for a surrogate one.

The PK change is forced by the semantics: ``trust_id`` must be NULL for a global
role, and Postgres forbids NULL in a primary-key column. Uniqueness is preserved
by two partial indexes rather than a plain UNIQUE constraint — Postgres treats
NULLs as distinct, so ``UNIQUE (user_id, role_id, trust_id)`` would happily allow
the same global role to be granted to the same user twice.

Revision ID: c3a7f1eb9402
Revises: 40f7934c6419
Create Date: 2026-09-22 16:02:11.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3a7f1eb9402'
down_revision: str | None = '40f7934c6419'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    # Surrogate PK: trust_id is nullable (global roles), and Postgres will not
    # accept a nullable column in a primary key.
    op.add_column('user_role', sa.Column('id', sa.Uuid(), nullable=True))
    op.execute('UPDATE user_role SET id = gen_random_uuid() WHERE id IS NULL')
    op.alter_column('user_role', 'id', nullable=False)

    op.add_column('user_role', sa.Column('trust_id', sa.Uuid(), nullable=True))

    op.drop_constraint('user_role_pkey', 'user_role', type_='primary')
    op.create_primary_key('user_role_pkey', 'user_role', ['id'])

    op.create_foreign_key(
        'user_role_trust_id_fkey', 'user_role', 'trust', ['trust_id'], ['id']
    )

    op.create_index(op.f('ix_user_role_user_id'), 'user_role', ['user_id'])
    op.create_index(op.f('ix_user_role_trust_id'), 'user_role', ['trust_id'])

    # Preserve the uniqueness the old composite PK gave us. Two partial indexes,
    # because a single UNIQUE over a nullable column would not constrain the
    # global-role case at all (NULL != NULL in Postgres).
    op.create_index(
        'uq_user_role_global',
        'user_role',
        ['user_id', 'role_id'],
        unique=True,
        postgresql_where=sa.text('trust_id IS NULL'),
    )
    op.create_index(
        'uq_user_role_per_trust',
        'user_role',
        ['user_id', 'role_id', 'trust_id'],
        unique=True,
        postgresql_where=sa.text('trust_id IS NOT NULL'),
    )


def downgrade() -> None:
    """Revert this revision.

    Trust-scoped grants cannot be represented by the old composite key, so they
    are dropped rather than silently promoted to global roles — a Trust Owner row
    surviving as a platform-wide grant would be a privilege escalation.
    """
    op.execute('DELETE FROM user_role WHERE trust_id IS NOT NULL')

    op.drop_index('uq_user_role_per_trust', table_name='user_role')
    op.drop_index('uq_user_role_global', table_name='user_role')
    op.drop_index(op.f('ix_user_role_trust_id'), table_name='user_role')
    op.drop_index(op.f('ix_user_role_user_id'), table_name='user_role')

    op.drop_constraint('user_role_trust_id_fkey', 'user_role', type_='foreignkey')

    op.drop_constraint('user_role_pkey', 'user_role', type_='primary')
    op.create_primary_key('user_role_pkey', 'user_role', ['user_id', 'role_id'])

    op.drop_column('user_role', 'trust_id')
    op.drop_column('user_role', 'id')
