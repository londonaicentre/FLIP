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
from unittest.mock import MagicMock

from sqlmodel import Session

from flip_api.db.models.main_models import Projects
from flip.domain.schemas.status import ProjectStatus
from flip.project_services.get_projects import get_projects_paginated_orm
from flip.utils.paging_utils import get_filter_details, get_paging_details

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
