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

"""Integration coverage of ``can_access_project`` against real Postgres.

**These tests must be integration tests.** ``can_access_project`` builds a SELECT whose defect was
a *missing join* — SQLAlchemy silently emitted ``FROM projects, project_user_access``, a cartesian
product in which any access row belonging to the caller satisfied the predicate for any project.
The unit suite stubs ``db.exec(...).first()`` to fake the allow/deny outcome, so it never compiles
or executes the statement and stays green against the vulnerable code. Only a real database
distinguishes the two.

The precondition is the important part: a Researcher gets a ``ProjectUserAccess`` row for every
project they create (``create_project``), so the exploit needed nothing but a project of one's own.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from flip_api.auth.access_manager import can_access_project
from flip_api.db.models.main_models import ProjectUserAccess
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.schemas.status import ProjectStatus
from tests.integration.conftest import admin_user, override_verify_token_as

__all__ = ["admin_user"]


@pytest.fixture
def researcher(session: Session) -> UUID:
    """A plain Researcher: can create projects, holds no CAN_MANAGE_PROJECTS short-circuit."""
    user_id = uuid4()
    session.add(UserRole(user_id=user_id, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    return user_id


@pytest.fixture
def own_project(session: Session, project_factory, researcher: UUID):
    """A project the researcher owns, with the access row create_project would have inserted."""
    project = project_factory.build(owner_id=researcher, deleted=False, status=ProjectStatus.UNSTAGED)
    session.add(project)
    session.commit()
    session.add(ProjectUserAccess(project_id=project.id, user_id=researcher))
    session.commit()

    return project


@pytest.fixture
def victim_project(session: Session, project_factory):
    """A project owned by someone else, with no access row for the researcher."""
    project = project_factory.build(owner_id=uuid4(), deleted=False, status=ProjectStatus.UNSTAGED)
    session.add(project)
    session.commit()

    return project


def test_researcher_cannot_access_an_unrelated_project(session: Session, researcher, own_project, victim_project):
    """The regression lock: red before the join was added, green after.

    Holding an access row for one's *own* project must not confer access to anyone else's.
    """
    assert can_access_project(researcher, victim_project.id, session) is False


def test_owner_still_has_access_to_their_own_project(session: Session, researcher, own_project, victim_project):
    """Positive control — guards against over-fixing by dropping the owner clause."""
    assert can_access_project(researcher, own_project.id, session) is True


def test_member_has_access_to_a_project_they_were_added_to(session: Session, researcher, own_project, victim_project):
    """Positive control — guards against over-fixing to owner-only.

    Membership is granted by an access row for *that* project, which is exactly the distinction
    the missing join destroyed.
    """
    session.add(ProjectUserAccess(project_id=victim_project.id, user_id=researcher))
    session.commit()

    assert can_access_project(researcher, victim_project.id, session) is True


def test_user_with_no_rows_at_all_is_denied(session: Session, victim_project):
    assert can_access_project(uuid4(), victim_project.id, session) is False


def test_manage_projects_permission_still_short_circuits(session: Session, victim_project):
    """An admin's CAN_MANAGE_PROJECTS bypass is intentional and must survive the fix."""
    assert can_access_project(admin_user(session), victim_project.id, session) is True


def test_access_row_with_null_project_id_grants_nothing(session: Session, researcher, victim_project):
    """``ProjectUserAccess.project_id`` is nullable, and a NULL never satisfies a join condition.

    This is precisely the row shape that would resurrect the bug if the join were ever reverted to
    a comma-join, so it is worth pinning explicitly.
    """
    session.add(ProjectUserAccess(project_id=None, user_id=researcher))
    session.commit()

    assert can_access_project(researcher, victim_project.id, session) is False


def test_endpoint_returns_403_for_an_unrelated_project(
    session: Session, client: TestClient, researcher, own_project, victim_project
):
    """The 403 must actually reach the wire, not just the helper's return value.

    ``/models`` rather than ``/projects/{id}``: the latter calls Cognito on the success path, which
    would drag a moto user pool into a test about SQL.
    """
    override_verify_token_as(researcher)

    assert client.get(f"/api/projects/{victim_project.id}/models").status_code == 403


def test_endpoint_returns_200_for_the_users_own_project(
    session: Session, client: TestClient, researcher, own_project, victim_project
):
    override_verify_token_as(researcher)

    assert client.get(f"/api/projects/{own_project.id}/models").status_code == 200
