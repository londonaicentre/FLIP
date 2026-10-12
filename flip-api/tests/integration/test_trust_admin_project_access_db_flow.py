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

"""A Trust Admin may READ a project staged at their trust (FLIP#1258), against real rows.

Deciding on a project needs its details, so a Trust Admin — otherwise just a Researcher — can view the project and
its cohort query when the project is staged at their trust. Nowhere else, and never to modify it.
"""

from uuid import uuid4

import pytest

from flip_api.auth.access_manager import can_access_cohort_query, can_access_project, can_modify_project
from flip_api.db.models.main_models import Queries
from flip_api.db.models.user_models import RoleRef, UserRole
from flip_api.domain.schemas.status import ProjectStatus, TrustApprovalStatus


@pytest.fixture
def world(session, project_factory, trust_factory, project_trust_intersect_factory):
    """Trust T (Trust Admin ``admin``) and U. ``at_t`` is staged at T; ``at_u`` only at U; ``unstaged`` nowhere."""
    admin = uuid4()
    t, u = trust_factory.build(), trust_factory.build()
    at_t = project_factory.build(owner_id=uuid4(), status=ProjectStatus.STAGED, deleted=False)
    at_u = project_factory.build(owner_id=uuid4(), status=ProjectStatus.STAGED, deleted=False)
    unstaged = project_factory.build(owner_id=uuid4(), status=ProjectStatus.UNSTAGED, deleted=False)
    for row in (t, u, at_t, at_u, unstaged):
        session.add(row)
    session.flush()
    for project, trust in ((at_t, t), (at_u, u)):
        session.add(
            project_trust_intersect_factory.build(
                project_id=project.id, trust_id=trust.id, status=TrustApprovalStatus.PENDING
            )
        )
    query = Queries(name="q", query="SELECT 1", created_by=at_t.owner_id, project_id=at_t.id, queried_trust_ids=[t.id])
    other_query = Queries(
        name="q", query="SELECT 1", created_by=at_u.owner_id, project_id=at_u.id, queried_trust_ids=[u.id]
    )
    session.add_all([query, other_query])
    session.add(UserRole(user_id=admin, role_id=RoleRef.RESEARCHER.value))
    session.add(UserRole(user_id=admin, role_id=RoleRef.TRUST_ADMIN.value, trust_id=t.id))
    session.commit()
    return {
        "admin": admin,
        "at_t": at_t,
        "at_u": at_u,
        "unstaged": unstaged,
        "query": query,
        "other_query": other_query,
    }


def test_a_trust_admin_can_view_a_project_staged_at_their_trust(session, world):
    assert can_access_project(world["admin"], world["at_t"].id, session)
    assert can_access_cohort_query(world["admin"], world["query"].id, session)


def test_but_not_one_staged_only_at_another_trust(session, world):
    assert not can_access_project(world["admin"], world["at_u"].id, session)
    assert not can_access_cohort_query(world["admin"], world["other_query"].id, session)


def test_nor_an_unstaged_project(session, world):
    assert not can_access_project(world["admin"], world["unstaged"].id, session)


def test_nor_may_they_modify_it(session, world):
    assert not can_modify_project(world["admin"], world["at_t"].id, session)


def test_a_researcher_who_is_not_a_trust_admin_gains_nothing(session, world):
    researcher = uuid4()
    session.add(UserRole(user_id=researcher, role_id=RoleRef.RESEARCHER.value))
    session.commit()

    assert not can_access_project(researcher, world["at_t"].id, session)


def test_a_global_trust_admin_row_confers_no_access(session, world):
    """A Trust Admin row with no trust names no trust, so it opens no project."""
    stray = uuid4()
    session.add(UserRole(user_id=stray, role_id=RoleRef.TRUST_ADMIN.value, trust_id=None))
    session.commit()

    assert not can_access_project(stray, world["at_t"].id, session)
