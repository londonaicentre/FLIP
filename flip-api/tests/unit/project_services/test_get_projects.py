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
    session.exec.return_value.all.return_value = [project_1, project_2]
    session.exec.return_value.one_or_none.return_value = 2

    project_response = get_projects_paginated_orm(
        session=session,
        user_id=user_id,
        paging_details=paging_details,
        filter_details=get_filter_details(),
    )

    assert len(project_response.data) == 2
    assert project_response.total_rows == 2


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
