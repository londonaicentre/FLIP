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

"""grant trust owner to existing admins at existing trusts

Approval became a site decision (FLIP#1258): ``CAN_APPROVE_FOR_TRUST`` is required AT the
trust being approved for, and the platform-wide ``CAN_APPROVE_PROJECTS`` grant no longer
satisfies it. That is correct, but it removes an ability hub administrators had in every
deployed instance, so this migration preserves it as an ordinary, revocable grant: every
user holding the Admin role is made a Trust Owner of each trust that exists at upgrade
time, as that trust.

Why a migration and not a seed: ``entrypoint.sh`` runs ``seed_essential_data.py`` on every
container start, and the seeders are idempotent by construction. A seeded grant would be
re-created on the next deploy after an operator revoked it, which silently defeats the
whole point of storing it per trust — so this runs exactly once, and a revocation sticks.

Scope, deliberately:

* Trusts are those that exist NOW. A trust registered later does not inherit any admin
  ownership, so new sites start owing their authority to themselves.
* Users are those holding Admin NOW. A later promotion does not silently confer ownership
  of every trust; that stays an explicit act (`POST /admin/trusts/{id}/owners`).

This is a grant, not a widening of the permission check: it does not touch
``TRUST_SCOPED_PERMISSIONS`` or ``has_trust_permissions``. Administrators are Trust Owners
by ordinary role assignment, so an operator can revoke any of these rows through the
owners API and the power then sits with the site. Nothing in the check needs to change for
that to work.

Downgrade is necessarily approximate. These rows are indistinguishable from Trust Owner
grants made afterwards through the API, and removing the wrong one would silently strip a
site of its owner. It therefore removes only grants belonging to users who still hold
Admin, which is the set this migration would create again on re-upgrade — and it may
remove an admin's grant that was made through the API rather than here. Both directions
are recorded in the log so an operator can reconcile. A precise downgrade would need a
column marking a row's origin, which is not worth a permanent schema wart for a one-shot
backfill of grants that are meant to be removed.

Revision ID: d5b2c8f04a17
Revises: c3a7f1eb9402
Create Date: 2026-09-24 09:14:53.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b2c8f04a17"
down_revision: str | None = "c3a7f1eb9402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Every user with a global Admin grant, made a Trust Owner of every existing trust.
#
# Written as one INSERT ... SELECT rather than a read-modify-write loop: it is a single
# statement, so it is atomic on its own and cannot half-apply. `gen_random_uuid()` fills
# the surrogate PK added by FLIP#1260; it is core Postgres since 13 (no pgcrypto needed).
#
# Both early-outs matter on a FRESH database, where migrations run before the seeders: if
# either the Admin role, the Trust Owner role, or any trust does not exist yet, the query
# inserts nothing and the deployment is unchanged. Nothing is skipped that should have run
# — a fresh install has no users to backfill, and the seeders create the roles immediately
# afterwards. `ON CONFLICT` targets the partial unique index for a trust-scoped row, so a
# re-run against an already-migrated database is a no-op rather than an error.
_GRANT_TRUST_OWNER_TO_ADMINS = """
INSERT INTO user_role (id, user_id, role_id, trust_id)
SELECT gen_random_uuid(), admin_holders.user_id, owner_role.id, t.id
FROM (SELECT DISTINCT ur.user_id
      FROM user_role ur
      JOIN roles r ON r.id = ur.role_id
      WHERE r.name = 'Admin' AND ur.trust_id IS NULL) AS admin_holders
CROSS JOIN (SELECT id FROM roles WHERE name = 'Trust Owner') AS owner_role
CROSS JOIN (SELECT id FROM trust) AS t
ON CONFLICT (user_id, role_id, trust_id) WHERE trust_id IS NOT NULL DO NOTHING
"""

# The mirror, restricted to users who STILL hold Admin — see the module docstring on why a
# precise reversal is impossible and why this conservative set is the right one.
_REVOKE_TRUST_OWNER_FROM_ADMINS = """
DELETE FROM user_role
WHERE trust_id IS NOT NULL
  AND role_id = (SELECT id FROM roles WHERE name = 'Trust Owner')
  AND user_id IN (SELECT DISTINCT ur.user_id
                  FROM user_role ur
                  JOIN roles r ON r.id = ur.role_id
                  WHERE r.name = 'Admin' AND ur.trust_id IS NULL)
"""


# New members for the native `trustauditaction` enum. Spelled out here rather than imported
# from the application: a migration has to keep meaning the same thing years from now, and
# an import would silently change with the code it depends on.
_NEW_AUDIT_ACTIONS = ("OWNER_ADDED", "OWNER_REMOVED")


def upgrade() -> None:
    """Make every Admin a Trust Owner of every trust that exists at upgrade time."""
    connection = op.get_bind()

    # The audit actions first. `trusts_audit.action` is a NATIVE Postgres enum
    # (`trustauditaction`), so a new member has to be added to the type — declaring it in
    # the Python enum alone is not enough, and the insert fails with
    # "invalid input value for enum" at runtime. Postgres 12+ allows ALTER TYPE ADD VALUE
    # inside a transaction provided the new value is not *used* in that same transaction;
    # this migration only adds, and the application uses them from its next statement on,
    # which is why the grant below can follow in the same transaction.
    #
    # IF NOT EXISTS keeps a re-run harmless. Downgrade cannot reverse these: Postgres has
    # no DROP VALUE, and rebuilding the type to remove them would rewrite every existing
    # audit row — not worth it for two labels that are inert once nothing writes them.
    for member in _NEW_AUDIT_ACTIONS:
        connection.execute(sa.text(f"ALTER TYPE trustauditaction ADD VALUE IF NOT EXISTS '{member}'"))

    result = connection.execute(sa.text(_GRANT_TRUST_OWNER_TO_ADMINS))
    if result.rowcount:
        # Logged, not silent: an operator auditing why a hub administrator holds trust
        # authority months from now needs to find that it came from this upgrade.
        print(f"[migration d5b2c8f04a17] granted Trust Owner to admins at existing trusts: {result.rowcount} row(s)")


def downgrade() -> None:
    """Remove the grants this migration created, for users who are still Admins."""
    connection = op.get_bind()

    result = connection.execute(sa.text(_REVOKE_TRUST_OWNER_FROM_ADMINS))
    print(f"[migration d5b2c8f04a17] removed admin-held Trust Owner grants: {result.rowcount} row(s)")
