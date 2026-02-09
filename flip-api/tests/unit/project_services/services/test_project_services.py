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

from datetime import datetime
from typing import List
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from psycopg2 import DatabaseError

from flip_api.db.models.main_models import (
    Projects,
    Queries,
    QueryStats,
    Trust,
    XNATProjectStatus,
)
from flip_api.domain.interfaces.project import (
    IProjectApproval,
    IProjectDetails,
    IProjectQuery,
    IProjectResponse,
    IReimportQuery,
)
from flip_api.domain.schemas.actions import ProjectAuditAction
from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.domain.schemas.status import (
    ProjectStatus,
    XNATImageStatus,
)
from flip_api.project_services.services.project_services import (
    approve_project,
    create_project,
    delete_project,
    edit_project_service,
    get_approved_trusts_for_project,
    get_project,
    get_project_models_service,
    get_reimport_queries_service,
    get_users_with_access,
    stage_project_service,
    unstage_project_service,
    update_project_status,
)
from flip_api.utils.project_manager import get_project_by_id

MOCK_SERVICE_PATH = "flip_api.project_services.services.project_services"


@pytest.fixture
def sample_project_id() -> UUID:
    return uuid4()


@pytest.fixture
def sample_user_ids() -> List[UUID]:
    return [uuid4(), uuid4(), uuid4()]  # Sample user IDs as UUIDs


@pytest.fixture
def sample_project() -> Projects:
    return Projects(
        id=uuid4(),
        name="Test Project",
        description="Test Description",
        owner_id=uuid4(),
        status=ProjectStatus.UNSTAGED,
        creation_timestamp=datetime.utcnow(),
        deleted=False,
    )


@pytest.fixture
def sample_trust_ids() -> List[UUID]:
    return [uuid4(), uuid4(), uuid4()]


@pytest.fixture
def sample_iproject_details() -> IProjectDetails:
    return IProjectDetails(
        name="Updated Project",
        description="Updated Description",
        users=[uuid4(), uuid4(), uuid4()],  # Sample user IDs as UUIDs,
    )


class TestCreateProject:
    def test_create_project_success(self, mock_db_session: MagicMock, sample_user_ids: List[UUID]):
        payload = ProjectDetails(name="New Project", description="Project Description", users=sample_user_ids)
        current_user_id = uuid4()

        with patch(f"{MOCK_SERVICE_PATH}.update_project_user_access") as mock_update_access:
            mock_db_session.flush.return_value = None
            mock_db_session.commit.return_value = None

            result = create_project(payload, current_user_id, mock_db_session)

            assert isinstance(result, UUID)
            mock_db_session.add.assert_called()
            mock_db_session.flush.assert_called()
            mock_db_session.commit.assert_called()
            mock_update_access.assert_called_once()

    def test_create_project_exception_handling(self, mock_db_session: MagicMock):
        payload = ProjectDetails(name="Test", description="Test", users=[])
        current_user_id = uuid4()

        mock_db_session.flush.side_effect = DatabaseError("Database error")

        with pytest.raises(HTTPException, match="Failed to create project: Database error"):
            create_project(payload, current_user_id, mock_db_session)

        mock_db_session.rollback.assert_called_once()


class TestDeleteProject:
    def test_delete_project_success(self, mock_db_session: MagicMock, sample_project: Projects):
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project

        with (
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit,
            patch(f"{MOCK_SERVICE_PATH}.delete_models") as mock_delete_models,
        ):
            mock_delete_models.return_value = 2

            delete_project(project_id, current_user_id, mock_db_session)

            assert sample_project.deleted is True
            mock_db_session.add.assert_called_with(sample_project)
            mock_db_session.flush.assert_called_once()
            mock_audit.assert_called_once_with(
                project_id=project_id,
                action=ProjectAuditAction.DELETE,
                user_id=current_user_id,
                session=mock_db_session,
            )
            mock_delete_models.assert_called_once()

    def test_delete_project_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()
        current_user_id = uuid4()

        mock_db_session.get.return_value = None

        with pytest.raises(HTTPException, match=f"Failed to delete project: Project with ID {project_id} not found."):
            delete_project(project_id, current_user_id, mock_db_session)

    def test_delete_project_already_deleted(self, mock_db_session: MagicMock, sample_project: Projects):
        sample_project.deleted = True
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project

        delete_project(project_id, current_user_id, mock_db_session)

        # Should return early without further processing
        mock_db_session.flush.assert_not_called()

    def test_delete_project_exception_handling(self, mock_db_session: MagicMock):
        project_id = uuid4()
        current_user_id = uuid4()

        mock_db_session.get.side_effect = DatabaseError("Database error")

        with pytest.raises(HTTPException, match="Failed to delete project: Database error"):
            delete_project(project_id, current_user_id, mock_db_session)

        mock_db_session.rollback.assert_called_once()


class TestEditProjectService:
    def test_edit_project_service_success(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_iproject_details: IProjectDetails
    ):
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project
        mock_db_session.exec.return_value.all.return_value = []

        with (
            patch(f"{MOCK_SERVICE_PATH}.update_project_user_access") as mock_update_access,
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit,
        ):
            edit_project_service(project_id, sample_iproject_details, current_user_id, mock_db_session)

            assert sample_project.name == sample_iproject_details.name
            assert sample_project.description == sample_iproject_details.description
            mock_db_session.add.assert_called_with(sample_project)
            mock_db_session.flush.assert_called_once()
            mock_update_access.assert_called_once()
            mock_audit.assert_called_once_with(
                project_id=project_id,
                action=ProjectAuditAction.EDIT,
                user_id=current_user_id,
                session=mock_db_session,
            )

    def test_edit_project_service_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()
        current_user_id = uuid4()
        payload = IProjectDetails(name="Test", description="Test", users=[])

        mock_db_session.get.return_value = None

        with pytest.raises(
            HTTPException,
            match=f"Failed to edit project: Project {project_id} does not exist or is deleted, cannot edit.",
        ):
            edit_project_service(project_id, payload, current_user_id, mock_db_session)

    def test_edit_project_service_deleted_project(self, mock_db_session: MagicMock, sample_project: Projects):
        sample_project.deleted = True
        project_id = sample_project.id
        current_user_id = uuid4()
        payload = IProjectDetails(name="Test", description="Test", users=[])

        mock_db_session.get.return_value = sample_project

        with pytest.raises(
            HTTPException,
            match=f"Failed to edit project: Project {project_id} does not exist or is deleted, cannot edit.",
        ):
            edit_project_service(project_id, payload, current_user_id, mock_db_session)

    def test_edit_project_service_exception_handling(self, mock_db_session: MagicMock):
        project_id = uuid4()
        current_user_id = uuid4()
        payload = IProjectDetails(name="Test", description="Test", users=[])

        mock_db_session.get.side_effect = DatabaseError("Database error")

        with pytest.raises(HTTPException, match="Failed to edit project: Database error"):
            edit_project_service(project_id, payload, current_user_id, mock_db_session)

        mock_db_session.rollback.assert_called_once()


class TestApproveProject:
    def test_approve_project_success(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: List[UUID]
    ):
        project_approval = IProjectApproval(project_id=sample_project.id, trust_ids=sample_trust_ids)
        user_id = uuid4()

        mock_db_session.get.return_value = sample_project
        mock_intersect = MagicMock()
        mock_db_session.exec.return_value.one_or_none.return_value = mock_intersect

        with (
            patch(f"{MOCK_SERVICE_PATH}.update_project_status") as mock_update_status,
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit,
        ):
            result = approve_project(mock_db_session, project_approval, user_id)

            assert result is True
            assert mock_intersect.approved is True
            mock_db_session.add.assert_called()
            mock_update_status.assert_called_once()
            mock_audit.assert_called_once_with(
                project_id=project_approval.project_id,
                action=ProjectAuditAction.APPROVE,
                user_id=user_id,
                session=mock_db_session,
            )
            mock_db_session.commit.assert_called_once()

    def test_approve_project_not_found(self, mock_db_session: MagicMock, sample_trust_ids: List[UUID]):
        project_approval = IProjectApproval(project_id=uuid4(), trust_ids=sample_trust_ids)
        user_id = uuid4()

        mock_db_session.get.return_value = None

        with pytest.raises(ValueError, match="does not exist"):
            approve_project(mock_db_session, project_approval, user_id)

    def test_approve_project_trust_not_found(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: List[UUID]
    ):
        project_approval = IProjectApproval(project_id=sample_project.id, trust_ids=sample_trust_ids)
        user_id = uuid4()

        mock_db_session.get.return_value = sample_project
        mock_db_session.exec.return_value.one_or_none.return_value = None

        result = approve_project(mock_db_session, project_approval, user_id)

        assert result is False
        mock_db_session.rollback.assert_called_once()


class TestStageProjectService:
    def test_stage_project_service_success(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: List[UUID]
    ):
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project

        with patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit:
            stage_project_service(project_id, sample_trust_ids, current_user_id, mock_db_session)

            mock_db_session.execute.assert_called()  # For delete statement
            mock_db_session.add_all.assert_called()
            mock_db_session.flush.assert_called()
            mock_audit.assert_called_once_with(
                project_id=project_id,
                action=ProjectAuditAction.STAGE,
                user_id=current_user_id,
                session=mock_db_session,
            )

    def test_stage_project_service_not_found(self, mock_db_session: MagicMock, sample_trust_ids: List[UUID]):
        project_id = uuid4()
        current_user_id = uuid4()

        mock_db_session.get.return_value = None

        with pytest.raises(ValueError, match="does not exist"):
            stage_project_service(project_id, sample_trust_ids, current_user_id, mock_db_session)

    def test_stage_project_service_empty_trust_ids(self, mock_db_session: MagicMock, sample_project: Projects):
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project

        stage_project_service(project_id, [], current_user_id, mock_db_session)

        mock_db_session.add_all.assert_not_called()


class TestUnstageProjectService:
    def test_unstage_project_service_success(self, mock_db_session: MagicMock, sample_project: Projects):
        project_id = sample_project.id
        current_user_id = uuid4()

        mock_db_session.get.return_value = sample_project
        mock_result = MagicMock()
        mock_result.count = 1
        mock_db_session.execute.return_value = mock_result

        with (
            patch(f"{MOCK_SERVICE_PATH}.update_project_status") as mock_update_status,
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit,
        ):
            unstage_project_service(project_id, current_user_id, mock_db_session)

            mock_db_session.execute.assert_called()
            mock_update_status.assert_called_once_with(
                project_id=project_id,
                new_status=ProjectStatus.UNSTAGED,
                session=mock_db_session,
            )
            mock_audit.assert_called_once_with(
                project_id=project_id,
                action=ProjectAuditAction.UNSTAGE,
                user_id=current_user_id,
                session=mock_db_session,
            )

    def test_unstage_project_service_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()
        current_user_id = uuid4()

        mock_db_session.get.return_value = None

        with pytest.raises(ValueError, match="does not exist"):
            unstage_project_service(project_id, current_user_id, mock_db_session)


class TestGetApprovedTrustsForProject:
    def test_get_approved_trusts_for_project_success(self, mock_db_session: MagicMock):
        project_id = uuid4()
        mock_results = [
            MagicMock(id=uuid4(), name="Trust 1", endpoint="endpoint1"),
            MagicMock(id=uuid4(), name="Trust 2", endpoint="endpoint2"),
        ]

        mock_db_session.execute.return_value.all.return_value = mock_results

        result = get_approved_trusts_for_project(project_id, mock_db_session)

        assert len(result) == 2
        assert all(isinstance(trust, Trust) for trust in result)

    def test_get_approved_trusts_for_project_empty(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.execute.return_value.all.return_value = []

        result = get_approved_trusts_for_project(project_id, mock_db_session)

        assert result == []


class TestGetProjectModelsService:
    def test_get_project_models_service_with_search(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.execute.return_value.all.return_value = []
        mock_db_session.execute.return_value.scalar_one_or_none.return_value = 0

        models, total = get_project_models_service(project_id, mock_db_session)

        assert models.data == []


class TestUpdateProjectStatus:
    def test_update_project_status_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()
        new_status = ProjectStatus.APPROVED

        mock_db_session.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            update_project_status(project_id, new_status, mock_db_session)


class TestGetProjectById:
    def test_get_project_by_id_success(self, mock_db_session: MagicMock, sample_project: Projects):
        project_id = sample_project.id

        mock_db_session.get.return_value = sample_project

        result = get_project_by_id(project_id, mock_db_session)

        assert result == sample_project

    def test_get_project_by_id_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.get.return_value = None

        result = get_project_by_id(project_id, mock_db_session)

        assert result is None

    def test_get_project_by_id_deleted(self, mock_db_session: MagicMock, sample_project: Projects):
        sample_project.deleted = True
        project_id = sample_project.id

        mock_db_session.get.return_value = sample_project

        result = get_project_by_id(project_id, mock_db_session)

        assert result is None


class TestGetReimportQueries:
    def test_successful_query(self):
        mock_session = MagicMock()

        ch_project_id = uuid4()
        trust_id = uuid4()

        query = Queries(id=uuid4(), name="Test Query", query="SELECT *", project_id=ch_project_id, created=None)
        xnat_project_status = XNATProjectStatus(
            id=uuid4(),
            xnat_project_id=uuid4(),
            project_id=ch_project_id,
            trust_id=trust_id,
            retrieve_image_status=XNATImageStatus.CREATED,
            last_reimport=datetime.utcnow(),
            reimport_count=1,
        )
        trust = Trust(id=trust_id, name="Example Trust", endpoint="https://trust.example.com")

        # Should be a List[tuple[Queries, XNATProjectStatus, Trust]]
        mock_session.exec.return_value.all.return_value = [(query, xnat_project_status, trust)]

        result = get_reimport_queries_service(max_reimport_count=5, session=mock_session)

        assert len(result) == 1
        assert isinstance(result[0], IReimportQuery)
        assert result[0].trust_name == "Example Trust"

    def test_empty_result(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        result = get_reimport_queries_service(max_reimport_count=5, session=mock_session)

        assert result == []

    def test_query_raises_exception(self):
        mock_session = MagicMock()
        mock_session.exec.side_effect = Exception("DB error")

        with pytest.raises(ValueError, match="DB error") as exc_info:
            get_reimport_queries_service(max_reimport_count=5, session=mock_session)

        assert "Error fetching reimport queries: DB error" in str(exc_info.value)


class TestGetProject:
    def test_get_project_success(self, mock_db_session: MagicMock):
        project_id = uuid4()
        query_id = uuid4()

        # Step 1: Mock project
        mock_project = Projects(
            id=project_id,
            name="Project 1",
            owner_id=uuid4(),
            deleted=False,
            description="desc",
            status="UNSTAGED",
        )
        # Step 2: Mock query
        mock_query = Queries(id=query_id, name="Test Query", query="SELECT *", project_id=project_id, created=None)
        # Step 3: Mock trust count
        mock_trust_count = 2
        # Step 4: Mock stats JSON
        stats_json = '{"TotalCount": 100}'
        mock_stats = QueryStats(id=uuid4(), query_id=query_id, stats=stats_json)

        # Chain of .exec().first() returns:
        mock_db_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_project)),  # first exec().first()
            MagicMock(first=MagicMock(return_value=mock_query)),  # second exec().first()
            MagicMock(first=MagicMock(return_value=2)),  # third exec().first()
            MagicMock(first=MagicMock(return_value=mock_stats)),  # fourth exec().first()
        ]

        result = get_project(project_id, mock_db_session)

        assert isinstance(result, IProjectResponse)
        assert result.id == project_id
        assert isinstance(result.query, IProjectQuery)
        assert result.query.trusts_queried == mock_trust_count
        assert result.query.total_cohort == 100

    def test_get_project_not_found(self, mock_db_session: MagicMock):
        project_id = uuid4()
        mock_db_session.exec.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            get_project(project_id, mock_db_session)

        assert exc_info.value.status_code == 404

    def test_get_project_no_query(self, mock_db_session: MagicMock):
        project_id = uuid4()
        mock_project = Projects(
            id=project_id,
            name="P",
            owner_id=uuid4(),
            deleted=False,
            description="desc",
            status="UNSTAGED",
        )

        mock_db_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_project)),  # first exec().first()
            MagicMock(first=MagicMock(return_value=[])),  # second exec().first() for query
        ]

        result = get_project(project_id, mock_db_session)

        assert isinstance(result, IProjectResponse)
        assert result.query is None

    def test_get_project_malformed_stats_json(self, mock_db_session: MagicMock):
        project_id = uuid4()
        query_id = uuid4()

        mock_project = Projects(
            id=project_id,
            name="Project X",
            owner_id=uuid4(),
            deleted=False,
            description="desc",
            status="UNSTAGED",
        )
        mock_query = Queries(id=query_id, name="Query X", query="bad sql", project_id=project_id, created=None)
        mock_stats = QueryStats(id=uuid4(), query_id=query_id, stats="{not-valid-json")

        # Chain of .exec().first() returns:
        mock_db_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_project)),  # first exec().first()
            MagicMock(first=MagicMock(return_value=mock_query)),  # second exec().first()
            MagicMock(first=MagicMock(return_value=2)),  # third exec().first()
            MagicMock(first=MagicMock(return_value=mock_stats)),  # fourth exec().first()
        ]

        result = get_project(project_id, mock_db_session)
        assert result.query is not None
        assert result.query.total_cohort == 0  # fallback due to parse failure


class TestGetUsersWithAccess:
    def test_get_users_with_access_success(self, mock_db_session: MagicMock):
        project_id = uuid4()
        user_ids = [uuid4(), uuid4()]

        mock_db_session.exec.return_value.all.return_value = user_ids

        result = get_users_with_access(project_id, mock_db_session)

        assert len(result) == 2
        assert all(isinstance(uid, UUID) for uid in result)

    def test_get_users_with_access_empty(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.exec.return_value.all.return_value = []

        result = get_users_with_access(project_id, mock_db_session)

        assert result == []
