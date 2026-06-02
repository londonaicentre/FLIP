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
    IApprovedTrust,
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
    get_project_query,
    get_reimport_queries_service,
    get_trusts_approval_status_for_project,
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
def sample_user_ids() -> list[UUID]:
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
def sample_trust_ids() -> list[UUID]:
    return [uuid4(), uuid4(), uuid4()]


@pytest.fixture
def sample_iproject_details() -> IProjectDetails:
    return IProjectDetails(
        name="Updated Project",
        description="Updated Description",
        users=[uuid4(), uuid4(), uuid4()],  # Sample user IDs as UUIDs,
    )


class TestCreateProject:
    def test_create_project_success(self, mock_db_session: MagicMock, sample_user_ids: list[UUID]):
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
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: list[UUID]
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

    def test_approve_project_not_found(self, mock_db_session: MagicMock, sample_trust_ids: list[UUID]):
        project_approval = IProjectApproval(project_id=uuid4(), trust_ids=sample_trust_ids)
        user_id = uuid4()

        mock_db_session.get.return_value = None

        with pytest.raises(ValueError, match="does not exist"):
            approve_project(mock_db_session, project_approval, user_id)

    def test_approve_project_trust_not_found(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: list[UUID]
    ):
        project_approval = IProjectApproval(project_id=sample_project.id, trust_ids=sample_trust_ids)
        user_id = uuid4()

        mock_db_session.get.return_value = sample_project
        mock_db_session.exec.return_value.one_or_none.return_value = None

        result = approve_project(mock_db_session, project_approval, user_id)

        assert result is False
        mock_db_session.rollback.assert_called_once()

    def test_approve_project_cancels_orphan_pending_tasks(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: list[UUID]
    ):
        """A trust the project is approved *without* (because it never
        responded) shouldn't keep an orphan PENDING task sitting in the
        queue. Approval flips those tasks to CANCELLED so the trust
        skips them on its next poll."""
        from flip_api.domain.schemas.status import TaskStatus

        project_approval = IProjectApproval(project_id=sample_project.id, trust_ids=sample_trust_ids)
        user_id = uuid4()
        mock_db_session.get.return_value = sample_project

        # Mock the trust-intersect lookup + latest_query + orphan tasks chain.
        # one_or_none() drives the per-trust approval loop; .first() and .all()
        # drive the new cancel block.
        mock_intersect = MagicMock()
        latest_query = MagicMock(id=uuid4())
        orphan_a = MagicMock(status=TaskStatus.PENDING)
        orphan_b = MagicMock(status=TaskStatus.PENDING)

        approval_exec = MagicMock()
        approval_exec.one_or_none.return_value = mock_intersect
        latest_query_exec = MagicMock()
        latest_query_exec.first.return_value = latest_query
        orphan_exec = MagicMock()
        orphan_exec.all.return_value = [orphan_a, orphan_b]

        # Per-trust approval calls fire first (one per sample trust), then
        # the latest-query lookup, then the orphan PENDING fetch.
        mock_db_session.exec.side_effect = (
            [approval_exec] * len(sample_trust_ids)
            + [latest_query_exec, orphan_exec]
        )

        with (
            patch(f"{MOCK_SERVICE_PATH}.update_project_status"),
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action"),
        ):
            assert approve_project(mock_db_session, project_approval, user_id) is True

        assert orphan_a.status == TaskStatus.CANCELLED
        assert orphan_b.status == TaskStatus.CANCELLED
        assert orphan_a.updated_at is not None
        assert orphan_b.updated_at is not None


class TestStageProjectService:
    def test_stage_project_service_success(
        self, mock_db_session: MagicMock, sample_project: Projects, sample_trust_ids: list[UUID]
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

    def test_stage_project_service_not_found(self, mock_db_session: MagicMock, sample_trust_ids: list[UUID]):
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


class TestGetProjectQuery:
    def test_returns_query_when_no_trusts_have_responded(self):
        """An empty queried_trust_ids should still return the query — the
        helper is only filtering out half-formed records (no id), not gating
        on trust responses."""
        query = IProjectQuery(id=uuid4(), name="Q", query="SELECT *", queried_trust_ids=[], total_cohort=0)
        project = MagicMock(spec=IProjectResponse)
        project.query = query

        result = get_project_query(project)

        assert result is query

    def test_returns_query_when_trusts_have_responded(self):
        query = IProjectQuery(
            id=uuid4(), name="Q", query="SELECT *", queried_trust_ids=[uuid4(), uuid4(), uuid4()], total_cohort=100
        )
        project = MagicMock(spec=IProjectResponse)
        project.query = query

        result = get_project_query(project)

        assert result is query

    def test_returns_none_when_no_query(self):
        project = MagicMock(spec=IProjectResponse)
        project.query = None

        result = get_project_query(project)

        assert result is None


class TestGetApprovedTrustsForProject:
    def test_get_approved_trusts_for_project_success(self, mock_db_session: MagicMock):
        project_id = uuid4()
        mock_results = [
            (uuid4(), "Trust 1"),
            (uuid4(), "Trust 2"),
        ]

        mock_db_session.exec.return_value.all.return_value = mock_results

        result = get_approved_trusts_for_project(project_id, mock_db_session)

        assert len(result) == 2
        assert all(isinstance(trust, Trust) for trust in result)

    def test_get_approved_trusts_for_project_empty(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.exec.return_value.all.return_value = []

        result = get_approved_trusts_for_project(project_id, mock_db_session)

        assert result == []


class TestGetTrustsApprovalStatusForProject:
    def test_get_trusts_approval_status_unpacks_six_columns(self, mock_db_session: MagicMock):
        project_id = uuid4()
        trust_a, trust_b, trust_c = uuid4(), uuid4(), uuid4()
        approved_at_a = datetime(2026, 3, 19, 10, 30, 0)
        mock_results = [
            # Now joined-and-grouped by project_id; first column is project_id.
            (project_id, trust_a, "Trust A", "TA", True, approved_at_a),
            (project_id, trust_b, "Trust B", "TB", False, None),
            # `approved` may come back as None for unstaged trusts; should normalise to False.
            # `code` and `approved_at` may be None on legacy rows.
            (project_id, trust_c, "Trust C", None, None, None),
        ]

        mock_db_session.exec.return_value.all.return_value = mock_results

        result = get_trusts_approval_status_for_project(project_id, mock_db_session)

        assert len(result) == 3
        assert all(isinstance(t, IApprovedTrust) for t in result)
        assert (result[0].id, result[0].name, result[0].code, result[0].approved) == (trust_a, "Trust A", "TA", True)
        assert result[0].approved_at == approved_at_a.isoformat(timespec="milliseconds")
        assert (result[1].id, result[1].name, result[1].code, result[1].approved) == (trust_b, "Trust B", "TB", False)
        assert result[1].approved_at is None
        assert (result[2].id, result[2].name, result[2].code, result[2].approved) == (trust_c, "Trust C", None, False)

    def test_get_trusts_approval_status_empty(self, mock_db_session: MagicMock):
        project_id = uuid4()

        mock_db_session.exec.return_value.all.return_value = []

        result = get_trusts_approval_status_for_project(project_id, mock_db_session)

        assert result == []


class TestGetProjectModelsService:
    def test_get_project_models_service_with_search(self, mock_db_session: MagicMock):
        project_id = uuid4()

        # The function makes two `exec` calls in order: count_stmt then query_stmt.
        count_result = MagicMock()
        count_result.first.return_value = 0
        query_result = MagicMock()
        query_result.all.return_value = []
        mock_db_session.exec.side_effect = [count_result, query_result]

        models, total = get_project_models_service(project_id, mock_db_session)

        assert models.data == []
        assert models.total_rows == 0
        assert mock_db_session.exec.call_count == 2


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

        query = Queries(
            id=uuid4(), name="Test Query", query="SELECT *", project_id=ch_project_id,
            created=None, created_by=uuid4(),
        )
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

        # Should be a list[tuple[Queries, XNATProjectStatus, Trust]]
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
        # Step 2: Mock query — queried_trust_ids is the persisted dispatched set.
        trust_ok_1 = uuid4()
        trust_ok_2 = uuid4()
        trust_errored = uuid4()
        mock_query = Queries(
            id=query_id, name="Test Query", query="SELECT *", project_id=project_id,
            created=None, created_by=uuid4(),
            queried_trust_ids=[trust_ok_1, trust_ok_2, trust_errored],
        )
        # Step 3: Mock (trust_id, data) pairs — successful + errored — so the
        # loader's queried/errored split is exercised end-to-end.
        result_rows = [
            (trust_ok_1, '{"record_count": 10, "data": [], "error": null}'),
            (trust_ok_2, '{"record_count": 5, "data": [], "error": null}'),
            (trust_errored, '{"record_count": 0, "data": [], "error": "OMOP timeout"}'),
        ]
        # Step 4: Mock stats JSON
        stats_json = '{"TotalCount": 100}'
        mock_stats = QueryStats(id=uuid4(), query_id=query_id, stats=stats_json)

        # Mock chain in order: Projects, Queries ⋈ UserProfile,
        # QueryResult.trust_id+.data, TrustTask (PENDING+CANCELLED), QueryStats.
        trust_pending = uuid4()
        trust_cancelled = uuid4()
        from flip_api.domain.schemas.status import TaskStatus
        mock_db_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_project)),  # select(Projects)
            MagicMock(first=MagicMock(return_value=(mock_query, "Alex Triay"))),  # Queries ⋈ UserProfile
            MagicMock(all=MagicMock(return_value=result_rows)),  # select(QueryResult.trust_id, .data)
            MagicMock(all=MagicMock(return_value=[
                (trust_pending, TaskStatus.PENDING),
                (trust_cancelled, TaskStatus.CANCELLED),
            ])),  # select(TrustTask.trust_id, .status) PENDING+CANCELLED
            MagicMock(first=MagicMock(return_value=mock_stats)),  # select(QueryStats)
        ]

        result = get_project(project_id, mock_db_session)

        assert isinstance(result, IProjectResponse)
        assert result.id == project_id
        assert isinstance(result.query, IProjectQuery)
        assert result.query.queried_trust_ids == [trust_ok_1, trust_ok_2, trust_errored]
        assert result.query.errored_trust_ids == [trust_errored]
        assert result.query.pending_trust_ids == [trust_pending]
        assert result.query.cancelled_trust_ids == [trust_cancelled]
        assert result.query.total_cohort == 100
        assert result.query.created_by == "Alex Triay"

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
            MagicMock(first=MagicMock(return_value=mock_project)),  # select(Projects)
            MagicMock(first=MagicMock(return_value=None)),  # Queries ⋈ UserProfile — no row
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
        mock_query = Queries(
            id=query_id, name="Query X", query="bad sql", project_id=project_id,
            created=None, created_by=uuid4(),
        )
        mock_stats = QueryStats(id=uuid4(), query_id=query_id, stats="{not-valid-json")

        mock_db_session.exec.side_effect = [
            MagicMock(first=MagicMock(return_value=mock_project)),  # select(Projects)
            MagicMock(first=MagicMock(return_value=(mock_query, None))),  # Queries ⋈ UserProfile
            MagicMock(all=MagicMock(return_value=[
                (uuid4(), '{"record_count": 1, "data": [], "error": null}'),
                (uuid4(), '{"record_count": 2, "data": [], "error": null}'),
            ])),  # select(QueryResult.trust_id, .data)
            MagicMock(all=MagicMock(return_value=[])),  # select(TrustTask.trust_id, .status) PENDING+CANCELLED
            MagicMock(first=MagicMock(return_value=mock_stats)),  # select(QueryStats)
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


# Helpers introduced by the connection-status PR — _classify_responded_trust_ids,
# update_project_user_access, plus warn paths in get_project_models_service /
# unstage_project_service.


class TestClassifyRespondedTrustIds:
    """One pass over QueryResult rows splits responded trusts into errored vs empty.

    A row whose `data` JSON fails to parse is treated as errored — better to surface the
    trust as red than silently swallow a corrupt response, which would let staging include
    a trust whose results we never validated. A non-errored trust with `record_count == 0`
    (a genuine zero or a privacy-suppressed count, #519) is empty: responded but with no
    usable cohort, so excluded from staging eligibility. `errored` and `empty` are disjoint.
    """

    def test_marks_malformed_json_as_errored(self):
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        tid = uuid4()
        responded, errored, empty = _classify_responded_trust_ids([(tid, "not-a-json-blob")], query_id=uuid4())

        assert responded == [tid]
        assert errored == [tid]
        assert empty == []

    def test_explicit_error_field_marks_trust_as_errored(self):
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        tid = uuid4()
        # An errored row is never double-flagged as empty even though its count is 0.
        responded, errored, empty = _classify_responded_trust_ids(
            [(tid, '{"record_count": 0, "error": "OMOP timeout"}')], query_id=uuid4()
        )

        assert responded == [tid]
        assert errored == [tid]
        assert empty == []

    def test_success_payload_is_neither_errored_nor_empty(self):
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        tid = uuid4()
        responded, errored, empty = _classify_responded_trust_ids(
            [(tid, '{"record_count": 7, "error": null}')], query_id=uuid4()
        )

        assert responded == [tid]
        assert errored == []
        assert empty == []

    def test_zero_record_count_is_flagged_empty(self):
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        tid = uuid4()
        responded, errored, empty = _classify_responded_trust_ids(
            [(tid, '{"record_count": 0, "error": null}')], query_id=uuid4()
        )

        assert responded == [tid]
        assert errored == []
        assert empty == [tid]

    def test_suppressed_count_is_flagged_empty(self):
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        tid = uuid4()
        responded, errored, empty = _classify_responded_trust_ids(
            [(tid, '{"record_count": 0, "suppressed": true, "error": null}')], query_id=uuid4()
        )

        assert responded == [tid]
        assert errored == []
        assert empty == [tid]

    def test_mixed_batch_partitions_in_one_pass_preserving_order(self):
        # Every distinct trust is "responded"; errored/empty are disjoint subsets in row order.
        from flip_api.project_services.services.project_services import _classify_responded_trust_ids

        ok, err, zero = uuid4(), uuid4(), uuid4()
        responded, errored, empty = _classify_responded_trust_ids(
            [
                (ok, '{"record_count": 7}'),
                (err, '{"record_count": 0, "error": "OMOP timeout"}'),
                (None, '{"record_count": 0}'),  # no trust id — skipped entirely
                (zero, '{"record_count": 0}'),
            ],
            query_id=uuid4(),
        )

        assert responded == [ok, err, zero]
        assert errored == [err]
        assert empty == [zero]


class TestUpdateProjectUserAccess:
    """Persists one `ProjectUserAccess` row per user id in a single commit.

    The endpoint that calls this (`approve_project` and friends) already
    guarantees the project exists, so there's no existence check here.
    """

    def test_persists_one_access_row_per_user(self, mock_db_session: MagicMock):
        from flip_api.db.models.main_models import ProjectUserAccess
        from flip_api.project_services.services.project_services import update_project_user_access

        project_id = uuid4()
        user_ids = [uuid4(), uuid4(), uuid4()]

        update_project_user_access(project_id, user_ids, mock_db_session)

        mock_db_session.add_all.assert_called_once()
        added = mock_db_session.add_all.call_args.args[0]
        assert len(added) == 3
        assert all(isinstance(entry, ProjectUserAccess) for entry in added)
        assert {entry.user_id for entry in added} == set(user_ids)
        mock_db_session.commit.assert_called_once()

    def test_empty_user_list_still_persists_an_empty_batch(self, mock_db_session: MagicMock):
        """An empty list isn't an error — clearing then re-applying is a
        legitimate code path (e.g. project owner removes all collaborators).
        """
        from flip_api.project_services.services.project_services import update_project_user_access

        update_project_user_access(uuid4(), [], mock_db_session)

        mock_db_session.add_all.assert_called_once_with([])
        mock_db_session.commit.assert_called_once()


def test_get_trusts_approval_status_for_projects_returns_empty_for_empty_input():
    """`get_trusts_approval_status_for_projects` short-circuits on empty input
    without issuing a SQL query — paginated callers can pass `[]` when their
    page is empty without paying for a wasted round-trip.
    """
    from unittest.mock import MagicMock

    from flip_api.project_services.services.project_services import (
        get_trusts_approval_status_for_projects,
    )

    session = MagicMock()

    result = get_trusts_approval_status_for_projects([], session)

    assert result == {}
    session.exec.assert_not_called()


class TestGetProjectModelsServiceSearch:
    """`get_project_models_service` accepts a `search` query-string param and
    appends a case-insensitive name+description filter to both the model and
    count queries.
    """

    def test_search_string_appends_filter_to_both_statements(self, mock_db_session: MagicMock):
        mock_db_session.exec.return_value.first.return_value = 0
        mock_db_session.exec.return_value.all.return_value = []

        get_project_models_service(
            project_id=uuid4(),
            session=mock_db_session,
            query_params={"search": "segmentation"},
        )

        # Two exec() calls: count then models. Both should carry the `like %seg%` predicate.
        compiled_stmts = [
            str(call.args[0].compile()).lower() for call in mock_db_session.exec.call_args_list
        ]
        for compiled in compiled_stmts:
            assert "like" in compiled
            assert "lower" in compiled


class TestUnstageWarnsOnZeroDeletes:
    def test_warns_when_no_rows_deleted_but_still_completes(
        self, mock_db_session: MagicMock, sample_project: Projects
    ):
        """A staged project whose trust-intersect rows were already gone should
        still flip back to UNSTAGED with a warn-log, not raise. Belt-and-braces
        for projects whose state drifted out-of-band.
        """
        project_id = sample_project.id
        mock_db_session.get.return_value = sample_project

        zero_result = MagicMock()
        zero_result.rowcount = 0
        mock_db_session.execute.return_value = zero_result

        with (
            patch(f"{MOCK_SERVICE_PATH}.update_project_status") as mock_update_status,
            patch(f"{MOCK_SERVICE_PATH}.audit_project_action") as mock_audit,
            patch(f"{MOCK_SERVICE_PATH}.logger") as mock_logger,
        ):
            unstage_project_service(project_id, uuid4(), mock_db_session)

            mock_logger.warn.assert_called_once()
            mock_update_status.assert_called_once()
            mock_audit.assert_called_once()
