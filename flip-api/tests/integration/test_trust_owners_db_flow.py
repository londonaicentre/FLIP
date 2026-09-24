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

"""Managing a trust's owners, against real role rows (FLIP#1258).

Approval became a site decision, and the per-trust grant it relies on is only usable if a
site can say who speaks for it. These cover that endpoint pair end to end: the grant, the
audit row it writes, the scope predicate (authority at trust A is not authority at trust
B, and a platform-wide Admin grant is not authority at all), and the last-owner guard.

Real Postgres through the shared session fixture, because every one of these decisions is
made by SQL: the scope predicate on ``user_role.trust_id`` and the count that decides
whether a removal would strand the trust.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select

from flip_api.db.models.main_models import TrustsAudit
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.domain.schemas.trusts import AddTrustOwner
from flip_api.trusts_services.manage_trust_owners import (
    add_trust_owner,
    list_trust_owners,
    remove_trust_owner,
)
from tests.integration.conftest import admin_user, override_verify_token_as


def _make_owner(session, trust_id):
    """Grant Trust Owner at a trust to a fresh user, returning that user's id."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.TRUST_OWNER.value, trust_id=trust_id))
    session.commit()
    return user_id


def test_add_then_list_round_trip(session, trust_factory):
    """A nominated owner appears in the list, and the grant is audited."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    owner_id = _make_owner(session, trust.id)
    nominee = uuid4()

    created = add_trust_owner(trust_id=trust.id, body=AddTrustOwner(user_id=nominee), db=session, token_id=owner_id)

    assert created.user_id == nominee
    listed = {o.user_id for o in list_trust_owners(trust_id=trust.id, db=session, token_id=owner_id)}
    assert listed == {owner_id, nominee}

    audits = session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == trust.id)).all()
    assert [a.action for a in audits] == [TrustAuditAction.OWNER_ADDED]


def test_holder_without_a_profile_row_is_still_listed(session, trust_factory):
    """The grant is what confers authority, so a missing profile must not hide an owner.

    A user_id with no ``user_profile`` row is normal — the profile is written by the
    registration flow, not by the grant — and silently omitting them would misrepresent
    who can act for the trust.
    """
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    owner_id = _make_owner(session, trust.id)

    listed = list_trust_owners(trust_id=trust.id, db=session, token_id=owner_id)

    assert [o.user_id for o in listed] == [owner_id]
    assert listed[0].name == ""


def test_adding_an_existing_owner_is_idempotent(session, trust_factory):
    """Re-nominating an owner returns them rather than erroring or duplicating."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    owner_id = _make_owner(session, trust.id)

    add_trust_owner(trust_id=trust.id, body=AddTrustOwner(user_id=owner_id), db=session, token_id=owner_id)
    add_trust_owner(trust_id=trust.id, body=AddTrustOwner(user_id=owner_id), db=session, token_id=owner_id)

    grants = session.exec(
        select(UserRole).where(UserRole.user_id == owner_id, UserRole.trust_id == trust.id)
    ).all()
    assert len(grants) == 1, "a repeat nomination must not create a second grant"


def test_hub_admin_cannot_nominate_owners(session, trust_factory):
    """A platform-wide Admin grant is not authority at a trust (the #1258 invariant).

    This is the property that keeps hub administration and trust authority separate. If it
    regressed, one hub administrator could nominate themselves owner of any trust and then
    approve its projects — exactly the power #1258 removed.
    """
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    admin_id = uuid4()
    session.add(UserRole(user_id=admin_id, role_id=RoleRef.ADMIN.value))
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        add_trust_owner(
            trust_id=trust.id, body=AddTrustOwner(user_id=admin_id), db=session, token_id=admin_id
        )

    assert exc_info.value.status_code == 403


def test_owner_of_another_trust_cannot_nominate_here(session, trust_factory):
    """Authority at trust A says nothing about trust B."""
    trust_a, trust_b = trust_factory.build(), trust_factory.build()
    session.add(trust_a)
    session.add(trust_b)
    session.commit()
    owner_of_a = _make_owner(session, trust_a.id)

    with pytest.raises(HTTPException) as exc_info:
        add_trust_owner(
            trust_id=trust_b.id, body=AddTrustOwner(user_id=uuid4()), db=session, token_id=owner_of_a
        )

    assert exc_info.value.status_code == 403


def test_unknown_trust_is_404_before_the_permission_check(session):
    """A non-existent trust is 404 for everyone, so the endpoint cannot map the federation."""
    with pytest.raises(HTTPException) as exc_info:
        list_trust_owners(trust_id=uuid4(), db=session, token_id=uuid4())

    assert exc_info.value.status_code == 404


def test_removing_an_owner_revokes_authority_and_is_audited(session, trust_factory):
    """Removal takes effect and is recorded — the handover this endpoint exists for."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    incumbent = _make_owner(session, trust.id)
    successor = _make_owner(session, trust.id)

    remove_trust_owner(trust_id=trust.id, user_id=incumbent, db=session, token_id=successor)

    listed = {o.user_id for o in list_trust_owners(trust_id=trust.id, db=session, token_id=successor)}
    assert listed == {successor}
    audits = session.exec(select(TrustsAudit).where(TrustsAudit.trust_id == trust.id)).all()
    assert [a.action for a in audits] == [TrustAuditAction.OWNER_REMOVED]


def test_cannot_remove_the_last_owner(session, trust_factory):
    """A trust left with no owner is stranded, so the removal is refused.

    Without this, one over-broad grant could be revoked into a state where nobody can
    approve projects, nominate a replacement, or edit the trust's policy — and the failure
    stays silent until someone tries to act.
    """
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    only_owner = _make_owner(session, trust.id)

    with pytest.raises(HTTPException) as exc_info:
        remove_trust_owner(trust_id=trust.id, user_id=only_owner, db=session, token_id=only_owner)

    assert exc_info.value.status_code == 409
    # Still an owner: the refusal must not have half-applied.
    assert list_trust_owners(trust_id=trust.id, db=session, token_id=only_owner)


def test_removing_a_non_owner_is_404(session, trust_factory):
    """Removing someone who holds no grant is 404, not a misleading success."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    owner = _make_owner(session, trust.id)

    with pytest.raises(HTTPException) as exc_info:
        remove_trust_owner(trust_id=trust.id, user_id=uuid4(), db=session, token_id=owner)

    assert exc_info.value.status_code == 404


def test_owners_list_is_scoped_to_the_trust_asked_about(session, trust_factory):
    """The list must not bleed grants from other trusts."""
    trust_a, trust_b = trust_factory.build(), trust_factory.build()
    session.add(trust_a)
    session.add(trust_b)
    session.commit()
    owner_of_a = _make_owner(session, trust_a.id)
    owner_of_b = _make_owner(session, trust_b.id)

    assert [o.user_id for o in list_trust_owners(trust_id=trust_a.id, db=session, token_id=owner_of_a)] == [owner_of_a]
    assert [o.user_id for o in list_trust_owners(trust_id=trust_b.id, db=session, token_id=owner_of_b)] == [owner_of_b]


def test_http_lifecycle_over_the_wire(client: TestClient, session, trust_factory):
    """POST, GET and DELETE behave as a client sees them.

    The direct-call tests above exercise the logic; this checks the pieces only the HTTP
    layer supplies — the authentication dependency, the path parameters, and the status
    codes (201 on create, 204 on delete) a client will branch on.
    """
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    owner_id = _make_owner(session, trust.id)
    nominee = uuid4()
    override_verify_token_as(owner_id)
    url = f"/api/admin/trusts/{trust.id}/owners"

    created = client.post(url, json={"user_id": str(nominee)})
    assert created.status_code == 201, created.text
    assert created.json()["user_id"] == str(nominee)

    session.expire_all()
    listed = client.get(url)
    assert listed.status_code == 200, listed.text
    assert {row["user_id"] for row in listed.json()} == {str(owner_id), str(nominee)}

    removed = client.delete(f"{url}/{nominee}")
    assert removed.status_code == 204, removed.text

    session.expire_all()
    after = client.get(url)
    assert {row["user_id"] for row in after.json()} == {str(owner_id)}


def test_http_hub_admin_is_refused(client: TestClient, session, trust_factory):
    """The #1258 invariant holds through the real request path, not just the function."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()
    admin_id = admin_user(session)
    override_verify_token_as(admin_id)

    response = client.post(f"/api/admin/trusts/{trust.id}/owners", json={"user_id": str(admin_id)})

    assert response.status_code == 403


def test_http_unknown_trust_is_404(client: TestClient, session):
    """A trust that does not exist is 404 for any authenticated caller."""
    override_verify_token_as(uuid4())

    response = client.get(f"/api/admin/trusts/{uuid4()}/owners")

    assert response.status_code == 404


def test_http_unauthenticated_is_refused(client: TestClient, session, trust_factory):
    """Without a token the endpoint does not fall through to the logic."""
    trust = trust_factory.build()
    session.add(trust)
    session.commit()

    response = client.get(f"/api/admin/trusts/{trust.id}/owners")

    assert response.status_code in (401, 403)
