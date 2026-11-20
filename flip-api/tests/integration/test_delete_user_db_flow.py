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

"""Deleting a user against real tables (FLIP#1996): what refuses it, what it removes, and what history shows after."""

from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlmodel import select

from flip_api.auth.dependencies import require_provisioned_user
from flip_api.db.models.main_models import Model, ProjectUserAccess
from flip_api.db.models.user_models import RoleRef, UserProfile, UserRole, UsersAudit
from flip_api.domain.schemas.users import CognitoUser
from flip_api.user_services.delete_user import delete_user
from flip_api.utils.user_roles import profile_display_name


@pytest.fixture
def admin_id(session):
    admin = uuid4()
    session.add(UserRole(user_id=admin, role_id=RoleRef.ADMIN.value))
    session.commit()
    return admin


@pytest.fixture
def leaver(session, fake_idp):
    """A disabled researcher with a profile, known to the identity provider."""
    user_id = uuid4()
    session.add(UserProfile(user_id=user_id, name="Ada Leaver", organisation="GSTT"))
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()
    fake_idp.get_user.return_value = CognitoUser(id=user_id, email="ada@example.com", is_disabled=True)
    return user_id


def _delete(session, user_id, admin_id, idp):
    return delete_user(user_id, None, session, admin_id, idp=idp)


def test_refused_while_the_user_owns_a_live_project(session, project_factory, leaver, admin_id, fake_idp):
    session.add(project_factory.build(name="Spleen study", owner_id=leaver, deleted=False))
    session.add(project_factory.build(name="Old study", owner_id=leaver, deleted=True))
    session.commit()

    with pytest.raises(HTTPException) as exc:
        _delete(session, leaver, admin_id, fake_idp)

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert "Spleen study" in exc.value.detail
    assert "Old study" not in exc.value.detail
    fake_idp.delete_user.assert_not_called()
    assert session.exec(select(UserRole).where(UserRole.user_id == leaver)).first() is not None


def test_refused_while_the_user_is_a_trust_admin(session, trust_factory, leaver, admin_id, fake_idp):
    trust = trust_factory.build(name=f"trust-{uuid4().hex}")
    session.add(trust)
    session.add(UserRole(user_id=leaver, role_id=RoleRef.TRUST_ADMIN.value, trust_id=trust.id))
    session.commit()

    with pytest.raises(HTTPException) as exc:
        _delete(session, leaver, admin_id, fake_idp)

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert f"Trust Admin of {trust.name}" in exc.value.detail
    fake_idp.delete_user.assert_not_called()


def test_refused_while_the_user_is_enabled(session, leaver, admin_id, fake_idp):
    fake_idp.get_user.return_value = CognitoUser(id=leaver, email="ada@example.com", is_disabled=False)

    with pytest.raises(HTTPException) as exc:
        _delete(session, leaver, admin_id, fake_idp)

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert "Disable the user" in exc.value.detail
    fake_idp.delete_user.assert_not_called()


def test_refused_for_the_callers_own_account(session, leaver, fake_idp):
    """The leaver is an admin deleting themselves: refused before anything changes."""
    session.add(UserRole(user_id=leaver, role_id=RoleRef.ADMIN.value))
    session.commit()

    with pytest.raises(HTTPException) as exc:
        _delete(session, leaver, leaver, fake_idp)

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert "your own account" in exc.value.detail
    fake_idp.delete_user.assert_not_called()
    assert session.get(UserProfile, leaver).deleted_at is None
    assert session.exec(select(UserRole).where(UserRole.user_id == leaver)).first() is not None


def test_delete_revokes_access_and_keeps_the_name_for_history(
    session, project_factory, model_factory, leaver, admin_id, fake_idp
):
    project = project_factory.build(owner_id=admin_id, deleted=False)
    session.add(project)
    session.commit()  # no ORM relationship orders the inserts below after it
    session.add(ProjectUserAccess(project_id=project.id, user_id=leaver))
    session.add(model_factory.build(project_id=project.id, owner_id=leaver, deleted=False))
    session.commit()

    assert _delete(session, leaver, admin_id, fake_idp) == {}

    fake_idp.delete_user.assert_called_once_with("ada@example.com")
    assert session.exec(select(UserRole).where(UserRole.user_id == leaver)).first() is None
    assert session.exec(select(ProjectUserAccess).where(ProjectUserAccess.user_id == leaver)).first() is None

    profile = session.get(UserProfile, leaver)
    assert profile.deleted_at is not None
    assert (profile.name, profile.organisation) == ("Ada Leaver", "")

    audit = session.exec(select(UsersAudit).where(UsersAudit.user_id == leaver)).one()
    assert (audit.action, audit.modified_by_user_id, audit.user_name) == ("Deleted user", admin_id, "Ada Leaver")

    owner_name = session.exec(
        select(profile_display_name()).select_from(Model).join(UserProfile, UserProfile.user_id == Model.owner_id)
    ).one()
    assert owner_name == "Former user (Ada Leaver)"

    # Even a token still minted for the deleted subject no longer counts as provisioned.
    with pytest.raises(HTTPException) as exc:
        require_provisioned_user(session, leaver)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
