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

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

from sqlmodel import Session

from flip_api.db.models.main_models import Projects
from flip_api.db.models.user_models import PermissionRef
from flip_api.domain.schemas.status import ProjectStatus
from flip_api.project_services.get_projects import get_projects_endpoint, get_projects_paginated_orm
from flip_api.utils.paging_utils import get_filter_details, get_paging_details

paging_details = get_paging_details()


def test_get_projects_paginated_orm_no_results():
    session = MagicMock(spec=Session)

    session.exec.return_value.all.return_value = []
    session.exec.return_value.one_or_none.return_value = None

    project_response = get_projects_paginated_orm(
        session=session,
        user_id=uuid.uuid4(),  # Give random user which won't have access to any projects
        paging_details=paging_details,
        filter_details=get_filter_details(),
    )

    assert project_response.data == []
    assert project_response.total_rows == 0


def test_get_projects_paginated_orm_some_results(user_id):
    session = MagicMock(spec=Session)
    # Mock return for two rows in fetchall() with total_rows=2 in the first row
    project_1 = Projects(
        name="Project 1",
        description="Test project 1",
        owner_id=user_id,
        status=ProjectStatus.APPROVED,
        creation_timestamp=datetime.utcnow(),
    )
    project_2 = Projects(
        name="Project 2",
        description="Test project 2",
        owner_id=user_id,
        status=ProjectStatus.APPROVED,
        creation_timestamp=datetime.utcnow(),
    )
    # get_projects_paginated_orm fires six SELECTs when projects exist:
    # projects (LEFT-joined to UserProfile so owner_name comes back in
    # the same round-trip) → count → batch trusts → batch latest-query →
    # batch STAGE-audit → batch user counts. The cohort loader
    # short-circuits when no queries exist.
    projects_call = MagicMock()
    projects_call.all.return_value = [(project_1, "Project Owner"), (project_2, "Project Owner")]
    count_call = MagicMock()
    count_call.one_or_none.return_value = 2
    trusts_call = MagicMock()
    trusts_call.all.return_value = []
    queries_call = MagicMock()
    queries_call.all.return_value = []
    stage_audit_call = MagicMock()
    stage_audit_call.all.return_value = []
    user_counts_call = MagicMock()
    user_counts_call.all.return_value = []
    session.exec.side_effect = [
        projects_call,
        count_call,
        trusts_call,
        queries_call,
        stage_audit_call,
        user_counts_call,
    ]

    project_response = get_projects_paginated_orm(
        session=session,
        user_id=user_id,
        paging_details=paging_details,
        filter_details=get_filter_details(),
    )

    assert len(project_response.data) == 2
    assert project_response.total_rows == 2


def test_get_projects_paginated_orm_populates_queried_trust_ids(user_id):
    """Regression: a project with a cohort query should surface the trust ids
    that responded (queried_trust_ids) alongside the count. Earlier loader
    used `func.distinct(QueryResult.trust_id)` next to `query_id` in the SELECT,
    which Postgres rejects — broke the whole projects list when any query
    existed."""
    session = MagicMock(spec=Session)

    project_id = uuid.uuid4()
    query_id = uuid.uuid4()
    trust_a = uuid.uuid4()
    trust_b = uuid.uuid4()

    project = Projects(
        id=project_id,
        name="P",
        description="d",
        owner_id=user_id,
        status=ProjectStatus.APPROVED,
        creation_timestamp=datetime.utcnow(),
    )

    projects_call = MagicMock()
    projects_call.all.return_value = [(project, "Alex Triay")]
    count_call = MagicMock()
    count_call.one_or_none.return_value = 1
    trusts_call = MagicMock()
    trusts_call.all.return_value = []
    queries_call = MagicMock()
    # Tuple shape matches the actual SELECT in _load_latest_queries_for_projects:
    # (Queries.id, project_id, name, query, created, UserProfile.name, queried_trust_ids)
    # `queried_trust_ids` is the persisted dispatched set — drives PerTrustResponse visibility.
    trust_never_responded = uuid.uuid4()
    queries_call.all.return_value = [(
        query_id, project_id, "Q", "SELECT 1", datetime.utcnow(), None,
        [trust_a, trust_b, trust_never_responded],
    )]
    pair_rows_call = MagicMock()
    # (query_id, trust_id, data) — trust_b errored, trust_a succeeded, the
    # never-responded one has no QueryResult row.
    pair_rows_call.all.return_value = [
        (query_id, trust_a, '{"record_count": 7, "data": [], "error": null}'),
        (query_id, trust_b, '{"record_count": 0, "data": [], "error": "OMOP timeout"}'),
    ]
    pending_rows_call = MagicMock()
    # (query_id, trust_id, status) — never-responded trust's task is PENDING.
    from flip_api.domain.schemas.status import TaskStatus
    pending_rows_call.all.return_value = [(query_id, trust_never_responded, TaskStatus.PENDING)]
    stats_call = MagicMock()
    stats_call.all.return_value = []
    stage_audit_call = MagicMock()
    stage_audit_call.all.return_value = []
    user_counts_call = MagicMock()
    user_counts_call.all.return_value = [(project_id, 3)]
    session.exec.side_effect = [
        projects_call,
        count_call,
        trusts_call,
        queries_call,
        pair_rows_call,
        pending_rows_call,
        stats_call,
        stage_audit_call,
        user_counts_call,
    ]

    response = get_projects_paginated_orm(
        session=session,
        user_id=user_id,
        paging_details=paging_details,
        filter_details=get_filter_details(),
    )

    assert len(response.data) == 1
    project_response = response.data[0]
    assert project_response.query is not None
    # queried_trust_ids is the dispatched set — includes the never-responded one.
    assert set(project_response.query.queried_trust_ids) == {trust_a, trust_b, trust_never_responded}
    assert project_response.query.errored_trust_ids == [trust_b]
    assert project_response.query.pending_trust_ids == [trust_never_responded]
    assert project_response.owner_name == "Alex Triay"
    assert project_response.user_count == 3


@patch("flip_api.project_services.get_projects.get_projects_paginated_orm")
@patch("flip_api.project_services.get_projects.has_permissions")
def test_get_projects_endpoint_researcher_filters_by_user_id(
    mock_has_permissions: MagicMock,
    mock_get_projects_paginated_orm: MagicMock,
):
    """A user without CAN_MANAGE_PROJECTS (e.g. a researcher) must have the per-user filter applied."""
    user_id = uuid.uuid4()
    request = MagicMock()
    request.query_params = {}
    session = MagicMock(spec=Session)
    mock_has_permissions.return_value = False
    mock_get_projects_paginated_orm.return_value = MagicMock(data=[], total_rows=0)

    get_projects_endpoint(request=request, session=session, user_id=user_id)

    mock_has_permissions.assert_called_once_with(user_id, [PermissionRef.CAN_MANAGE_PROJECTS], session)
    _, kwargs = mock_get_projects_paginated_orm.call_args
    assert kwargs["user_id"] == user_id


@patch("flip_api.project_services.get_projects.get_projects_paginated_orm")
@patch("flip_api.project_services.get_projects.has_permissions")
def test_get_projects_endpoint_admin_bypasses_user_filter(
    mock_has_permissions: MagicMock,
    mock_get_projects_paginated_orm: MagicMock,
):
    """A user with CAN_MANAGE_PROJECTS (e.g. an admin) lists every project — user_id=None is passed through."""
    user_id = uuid.uuid4()
    request = MagicMock()
    request.query_params = {}
    session = MagicMock(spec=Session)
    mock_has_permissions.return_value = True
    mock_get_projects_paginated_orm.return_value = MagicMock(data=[], total_rows=0)

    get_projects_endpoint(request=request, session=session, user_id=user_id)

    _, kwargs = mock_get_projects_paginated_orm.call_args
    assert kwargs["user_id"] is None
