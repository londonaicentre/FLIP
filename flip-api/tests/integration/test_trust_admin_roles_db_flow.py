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

"""Trust Admin grants written by the role save, against real user_role / trusts_audit rows (FLIP#1258).

A Trust Admin is stored as a global Researcher row plus a Trust Admin row at one trust, so these walk the
real tables: which rows exist after each save, and which audit rows the trust accumulates.
"""

from uuid import uuid4

import pytest
from sqlmodel import select

from flip_api.db.models.main_models import TrustsAudit
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.interfaces.user import IRoles
from flip_api.domain.schemas.actions import TrustAuditAction
from flip_api.user_services.retrieve_user_permissions import retrieve_user_permissions
from flip_api.user_services.set_user_roles import set_user_roles


@pytest.fixture
def admin_id(session):
    admin = uuid4()
    session.add(UserRole(user_id=admin, role_id=RoleRef.ADMIN.value))
    session.commit()
    return admin


@pytest.fixture
def make_trust(session, trust_factory):
    def _make():
        # Unique, not the factory's Faker word: the trust table survives between tests and delete_one_trust finds a
        # trust by name, so a word an earlier test used would delete that trust instead.
        trust = trust_factory.build(name=f"trust-{uuid4().hex}")
        session.add(trust)
        session.commit()
        return trust

    return _make


@pytest.fixture
def idp(fake_idp):
    """The identity provider knows every user these tests assign roles to."""
    fake_idp.get_username.return_value = "someone"
    return fake_idp


def _rows(session, user_id):
    return {(r.role_id, r.trust_id) for r in session.exec(select(UserRole).where(UserRole.user_id == user_id))}


def _audit(session, trust_id):
    rows = session.exec(
        select(TrustsAudit).where(TrustsAudit.trust_id == trust_id).order_by(TrustsAudit.audit_date)
    ).all()
    return [(r.action, r.subject_user_id) for r in rows]


def _make_trust_admin(session, user, trust, admin_id, idp):
    set_user_roles(user, IRoles(roles=[RoleRef.TRUST_ADMIN.value], trust_id=trust.id), session, admin_id, idp=idp)


def test_trust_admin_is_saved_as_researcher_plus_the_trust_grant(session, make_trust, admin_id, idp):
    trust = make_trust()
    user = uuid4()

    _make_trust_admin(session, user, trust, admin_id, idp)

    assert _rows(session, user) == {(RoleRef.RESEARCHER.value, None), (RoleRef.TRUST_ADMIN.value, trust.id)}
    assert _audit(session, trust.id) == [(TrustAuditAction.ADMIN_ADDED, user)]


def test_moving_a_trust_admin_audits_both_trusts(session, make_trust, admin_id, idp):
    old, new = make_trust(), make_trust()
    user = uuid4()
    _make_trust_admin(session, user, old, admin_id, idp)

    _make_trust_admin(session, user, new, admin_id, idp)

    assert _rows(session, user) == {(RoleRef.RESEARCHER.value, None), (RoleRef.TRUST_ADMIN.value, new.id)}
    assert (TrustAuditAction.ADMIN_REMOVED, user) in _audit(session, old.id)
    assert (TrustAuditAction.ADMIN_ADDED, user) in _audit(session, new.id)


def test_changing_to_another_role_removes_the_trust_grant(session, make_trust, admin_id, idp):
    trust = make_trust()
    user = uuid4()
    _make_trust_admin(session, user, trust, admin_id, idp)

    set_user_roles(user, IRoles(roles=[RoleRef.VIEWER.value]), session, admin_id, idp=idp)

    assert _rows(session, user) == {(RoleRef.VIEWER.value, None)}
    assert _audit(session, trust.id)[-1] == (TrustAuditAction.ADMIN_REMOVED, user)


def test_resaving_the_same_trust_admin_writes_no_second_audit(session, make_trust, admin_id, idp):
    trust = make_trust()
    user = uuid4()

    _make_trust_admin(session, user, trust, admin_id, idp)
    _make_trust_admin(session, user, trust, admin_id, idp)

    assert _audit(session, trust.id) == [(TrustAuditAction.ADMIN_ADDED, user)]


def test_permissions_name_the_trust_a_trust_admin_administers(session, make_trust, admin_id, idp):
    trust = make_trust()
    user = uuid4()
    _make_trust_admin(session, user, trust, admin_id, idp)

    response = retrieve_user_permissions(user, session, user)

    assert response.trust_admin_of is not None
    assert response.trust_admin_of.id == trust.id
    assert "CanCreateProjects" in response.permissions  # the Researcher half
    assert "CanApproveForTrust" not in response.permissions  # a trust grant is never listed platform-wide


def test_a_user_who_is_not_a_trust_admin_has_no_trust(session, admin_id):
    assert retrieve_user_permissions(admin_id, session, admin_id).trust_admin_of is None


def test_delete_trust_keeps_trust_admins_researcher_role(session, make_trust, admin_id, idp):
    """Deleting a trust removes its Trust Admin rows and nothing else: its admins stay Researchers."""
    from flip_api.scripts.delete_trust import delete_one_trust

    trust = make_trust()
    user = uuid4()
    _make_trust_admin(session, user, trust, admin_id, idp)

    delete_one_trust(trust.name, session)

    assert _rows(session, user) == {(RoleRef.RESEARCHER.value, None)}
