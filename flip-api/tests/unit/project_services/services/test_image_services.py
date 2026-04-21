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

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from flip_api.domain.interfaces.project import (
    IReimportQuery,
    IUpdateXnatProfile,
)
from flip_api.domain.interfaces.trust import ITrust
from flip_api.domain.schemas.projects import (
    ImagingProject,
    XnatProjectStatusInfo,
)
from flip_api.domain.schemas.status import XNATImageStatus
from flip_api.project_services.services.image_service import (
    _get_latest_imaging_status,
    base64_url_encode,
    delete_imaging_project,
    get_imaging_project_statuses,
    get_imaging_projects,
    get_xnat_project_status_info,
    has_pending_imaging_tasks,
    reimport_failed_studies,
    update_xnat_user_profile,
)

# Mocking paths
MOCK_SERVICE_PATH = "flip_api.project_services.services.image_service"
MOCK_LOGGER_PATH = f"{MOCK_SERVICE_PATH}.logger"
MOCK_GET_TRUSTS_PATH = f"{MOCK_SERVICE_PATH}.get_trusts"


@pytest.fixture
def sample_project_id() -> str:
    return str(uuid4())


@pytest.fixture
def sample_imaging_project_data(sample_project_id: str) -> ImagingProject:
    return ImagingProject(
        id=uuid4(),
        xnat_project_id=uuid4(),
        trust_id=uuid4(),
        retrieve_image_status=XNATImageStatus.CREATED,
        name="Test Trust XNAT",
        reimport_count=0,
    )


# --- get_imaging_projects ---
class TestGetImagingProjects:
    def test_success(self, mock_db_session: MagicMock, sample_project_id: UUID):
        db_row_data = [
            (uuid4(), uuid4(), uuid4(), "CREATED", "Trust XNAT 1", 0),
            (uuid4(), uuid4(), uuid4(), "DELETED", "Trust XNAT 2", 1),
        ]
        # Simulate db.exec(statement).all() returning list of tuples/rows
        mock_db_session.exec.return_value.all.return_value = db_row_data

        result = get_imaging_projects(sample_project_id, mock_db_session)

        assert len(result) == 2
        assert isinstance(result[0], ImagingProject)
        assert result[0].id == db_row_data[0][0]
        assert result[0].xnat_project_id == db_row_data[0][1]
        assert result[0].trust_id == db_row_data[0][2]
        assert result[0].retrieve_image_status == XNATImageStatus(db_row_data[0][3])
        assert result[0].name == db_row_data[0][4]
        assert result[0].reimport_count == db_row_data[0][5]

        mock_db_session.exec.assert_called_once()

    def test_includes_trusts_without_xnat_project(self, mock_db_session: MagicMock, sample_project_id: UUID):
        """Approved trusts without an XNATProjectStatus row should appear with None fields."""
        trust_id = uuid4()
        db_row_data = [
            (None, None, trust_id, None, "Trust Without XNAT", None),
        ]
        mock_db_session.exec.return_value.all.return_value = db_row_data

        result = get_imaging_projects(sample_project_id, mock_db_session)

        assert len(result) == 1
        assert result[0].trust_id == trust_id
        assert result[0].id is None
        assert result[0].xnat_project_id is None
        assert result[0].retrieve_image_status is None
        assert result[0].name == "Trust Without XNAT"
        assert result[0].reimport_count == 0

    @patch(MOCK_LOGGER_PATH)
    def test_db_error(self, mock_logger: MagicMock, mock_db_session: MagicMock, sample_project_id: UUID):
        db_error = Exception("DB Read Error")
        mock_db_session.exec.side_effect = db_error
        with pytest.raises(Exception, match="DB Read Error"):
            get_imaging_projects(sample_project_id, mock_db_session)
        mock_logger.error.assert_called_once()


# --- delete_imaging_project ---
class TestDeleteImagingProject:
    @patch(MOCK_LOGGER_PATH)
    def test_success(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
        sample_imaging_project_data: ImagingProject,
    ):
        result = delete_imaging_project(sample_imaging_project_data, mock_db_session)

        assert result is True
        # Should add a TrustTask and update status
        mock_db_session.add.assert_called_once()
        mock_db_session.execute.assert_called_once()
        mock_db_session.commit.assert_called_once()

    @patch(MOCK_LOGGER_PATH)
    def test_db_update_fails(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
        sample_imaging_project_data: ImagingProject,
    ):
        db_update_error = Exception("DB Update Failed")
        mock_db_session.execute.side_effect = db_update_error

        result = delete_imaging_project(sample_imaging_project_data, mock_db_session)

        assert result is False
        mock_logger.error.assert_called_with(
            f"Error queuing imaging project deletion: {db_update_error}", exc_info=True
        )
        mock_db_session.rollback.assert_called_once()
        mock_db_session.commit.assert_not_called()


# --- get_xnat_project_status_info ---
class TestGetXnatProjectStatusInfo:
    def test_success(self, mock_db_session: MagicMock):
        xnat_id = uuid4()
        db_row = (XNATImageStatus.CREATED, 1)  # retrieve_image_status, reimport_count
        mock_db_session.exec.return_value.one_or_none.return_value = db_row

        result = get_xnat_project_status_info(xnat_id, mock_db_session)

        assert isinstance(result, XnatProjectStatusInfo)
        assert result.retrieve_image_status == XNATImageStatus.CREATED
        assert result.reimport_count == 1
        mock_db_session.exec.assert_called_once()

    @patch(MOCK_LOGGER_PATH)
    def test_not_found(self, mock_logger: MagicMock, mock_db_session: MagicMock):
        xnat_id = uuid4()
        mock_db_session.exec.return_value.one_or_none.return_value = None

        result = get_xnat_project_status_info(xnat_id, mock_db_session)

        assert result is None
        mock_logger.error.assert_called_with(f"Could not get XNAT status for project id: {xnat_id}")

    @patch(MOCK_LOGGER_PATH)
    def test_db_error(self, mock_logger: MagicMock, mock_db_session: MagicMock):
        xnat_id = uuid4()
        db_error = Exception("DB Error")
        mock_db_session.exec.side_effect = db_error

        with pytest.raises(Exception, match="DB Error"):
            get_xnat_project_status_info(xnat_id, mock_db_session)
        mock_logger.error.assert_called_with(
            f"Unexpected error fetching XNAT project status for {xnat_id}: {db_error}", exc_info=True
        )


# --- get_imaging_project_statuses ---
class TestGetImagingProjectStatuses:
    @patch(f"{MOCK_SERVICE_PATH}._get_latest_imaging_status")
    @patch(f"{MOCK_SERVICE_PATH}.get_xnat_project_status_info")
    @patch(MOCK_LOGGER_PATH)
    def test_returns_statuses_and_queues_tasks(
        self,
        mock_logger: MagicMock,
        mock_get_xnat_status: MagicMock,
        mock_get_latest_status: MagicMock,
        mock_db_session: MagicMock,
    ):
        trust_id_1, trust_id_2 = uuid4(), uuid4()
        imaging_projects_list = [
            ImagingProject(
                id=uuid4(),
                xnat_project_id=uuid4(),
                trust_id=trust_id_1,
                retrieve_image_status=XNATImageStatus.CREATED,
                name="Trust1",
                reimport_count=0,
            ),
            ImagingProject(
                id=uuid4(),
                xnat_project_id=uuid4(),
                trust_id=trust_id_2,
                retrieve_image_status=XNATImageStatus.CREATED,
                name="Trust2",
                reimport_count=1,
            ),
        ]
        encoded_query = "ZXF1ZXJ5"

        mock_get_xnat_status.side_effect = [
            XnatProjectStatusInfo(retrieve_image_status=XNATImageStatus.CREATED, reimport_count=0),
            XnatProjectStatusInfo(retrieve_image_status=XNATImageStatus.RETRIEVE_COMPLETED, reimport_count=1),
        ]

        mock_get_latest_status.return_value = None

        # No existing pending/in-progress tasks
        mock_db_session.exec.return_value.first.return_value = None

        results = get_imaging_project_statuses(imaging_projects_list, encoded_query, mock_db_session)

        assert len(results) == 2
        assert results[0].trust_id == trust_id_1
        assert results[0].project_creation_completed is True
        assert results[1].trust_id == trust_id_2
        assert results[1].project_creation_completed is False

        assert mock_get_xnat_status.call_count == 2
        assert mock_get_latest_status.call_count == 2
        # Should have added task records and committed
        assert mock_db_session.add.call_count == 2
        mock_db_session.commit.assert_called_once()

    @patch(f"{MOCK_SERVICE_PATH}._get_latest_imaging_status")
    @patch(f"{MOCK_SERVICE_PATH}.get_xnat_project_status_info")
    @patch(MOCK_LOGGER_PATH)
    def test_returns_import_status_from_completed_task(
        self,
        mock_logger: MagicMock,
        mock_get_xnat_status: MagicMock,
        mock_get_latest_status: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should populate import_status from the latest completed GET_IMAGING_STATUS task."""
        from flip_api.domain.interfaces.project import IImagingImportStatus

        trust_id = uuid4()
        imaging_projects_list = [
            ImagingProject(
                id=uuid4(),
                xnat_project_id=uuid4(),
                trust_id=trust_id,
                retrieve_image_status=XNATImageStatus.CREATED,
                name="Trust1",
                reimport_count=0,
            ),
        ]

        mock_get_xnat_status.return_value = XnatProjectStatusInfo(
            retrieve_image_status=XNATImageStatus.CREATED, reimport_count=0
        )
        mock_get_latest_status.return_value = IImagingImportStatus(
            successful=10, failed=2, processing=3, queued=5, queueFailed=1
        )
        mock_db_session.exec.return_value.first.return_value = None

        results = get_imaging_project_statuses(imaging_projects_list, "ZXF1ZXJ5", mock_db_session)

        assert len(results) == 1
        assert results[0].import_status is not None
        assert results[0].import_status.successful_count == 10
        assert results[0].import_status.failed_count == 2
        assert results[0].import_status.processing_count == 3
        assert results[0].import_status.queued_count == 5
        assert results[0].import_status.queue_failed_count == 1

    @patch(f"{MOCK_SERVICE_PATH}._get_latest_imaging_status")
    @patch(f"{MOCK_SERVICE_PATH}.get_xnat_project_status_info")
    @patch(MOCK_LOGGER_PATH)
    def test_skips_xnat_lookup_for_trust_without_project(
        self,
        mock_logger: MagicMock,
        mock_get_xnat_status: MagicMock,
        mock_get_latest_status: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Trusts without an XNAT project should not trigger status lookups or task queuing."""
        trust_with_xnat = uuid4()
        trust_without_xnat = uuid4()
        imaging_projects_list = [
            ImagingProject(
                id=uuid4(),
                xnat_project_id=uuid4(),
                trust_id=trust_with_xnat,
                retrieve_image_status=XNATImageStatus.CREATED,
                name="Trust With XNAT",
                reimport_count=0,
            ),
            ImagingProject(
                trust_id=trust_without_xnat,
                name="Trust Without XNAT",
            ),
        ]

        mock_get_xnat_status.return_value = XnatProjectStatusInfo(
            retrieve_image_status=XNATImageStatus.CREATED, reimport_count=0
        )
        mock_get_latest_status.return_value = None
        mock_db_session.exec.return_value.first.return_value = None

        results = get_imaging_project_statuses(imaging_projects_list, "ZXF1ZXJ5", mock_db_session)

        assert len(results) == 2
        # Trust with XNAT project should have status looked up
        assert results[0].project_creation_completed is True
        assert mock_get_xnat_status.call_count == 1
        assert mock_get_latest_status.call_count == 1
        # Trust without XNAT project should show as not created
        assert results[1].trust_id == trust_without_xnat
        assert results[1].project_creation_completed is False
        assert results[1].import_status is None


# --- update_xnat_user_profile ---
class TestUpdateXnatUserProfile:
    @patch(MOCK_GET_TRUSTS_PATH)
    @patch(MOCK_LOGGER_PATH)
    def test_queues_tasks_for_all_trusts(
        self,
        mock_logger: MagicMock,
        mock_get_trusts: MagicMock,
        mock_db_session: MagicMock,
    ):
        trust_list = [
            ITrust(id=uuid4(), name="Trust1"),
            ITrust(id=uuid4(), name="Trust2"),
        ]
        mock_get_trusts.return_value = trust_list

        request_data = IUpdateXnatProfile(email="user@example.com", enabled=True)

        update_xnat_user_profile(request_data, mock_db_session)

        assert mock_get_trusts.call_count == 1
        assert mock_db_session.add.call_count == 2
        mock_db_session.commit.assert_called_once()
        mock_logger.info.assert_called_once()


# --- reimport_failed_studies ---
class TestReimportFailedStudies:
    @patch(f"{MOCK_SERVICE_PATH}.base64_url_encode")
    @patch(MOCK_LOGGER_PATH)
    def test_reimport_logic(
        self,
        mock_logger: MagicMock,
        mock_b64_encode: MagicMock,
        mock_db_session: MagicMock,
    ):
        project_reimport_rate_minutes = 60  # 1 hour

        queries = [
            # This one should not run because last_reimport is within the rate limit
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT * FROM studies",
                xnat_project_id=uuid4(),
                last_reimport=datetime.now(timezone.utc) - timedelta(minutes=30),
                trust_id=uuid4(),
                trust_name="T1",
            ),
            # This one should run because last_reimport is outside the rate limit
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT * FROM studies_2",
                xnat_project_id=uuid4(),
                last_reimport=datetime.now(timezone.utc) - timedelta(minutes=90),
                trust_id=uuid4(),
                trust_name="T2",
            ),
            # No last reimport, so it should run
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT * FROM studies_3",
                xnat_project_id=uuid4(),
                last_reimport=None,
                trust_id=uuid4(),
                trust_name="T3",
            ),
        ]

        mock_b64_encode.side_effect = lambda q: base64.urlsafe_b64encode(q.encode()).decode()

        # Mock DB returning XNATProjectStatus for eligible queries
        mock_xnat_status = MagicMock()
        mock_xnat_status.last_reimport = None
        mock_xnat_status.reimport_count = 0
        mock_db_session.exec.return_value.one_or_none.return_value = mock_xnat_status

        result = reimport_failed_studies(queries, mock_db_session, project_reimport_rate_minutes)

        assert result is True
        assert mock_b64_encode.call_count == 3
        # Two eligible queries should have been queued (Q2 and Q3)
        assert mock_db_session.add.call_count == 2
        mock_db_session.commit.assert_called_once()

    @patch(f"{MOCK_SERVICE_PATH}.base64_url_encode")
    @patch(MOCK_LOGGER_PATH)
    def test_reimport_record_not_found(
        self,
        mock_logger: MagicMock,
        mock_b64_encode: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should log error and continue when XNATProjectStatus record not found."""
        queries = [
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT 1",
                xnat_project_id=uuid4(),
                last_reimport=None,
                trust_id=uuid4(),
                trust_name="T1",
            ),
        ]
        mock_b64_encode.side_effect = lambda q: "encoded"
        mock_db_session.exec.return_value.one_or_none.return_value = None

        result = reimport_failed_studies(queries, mock_db_session, 0)

        assert result is False
        mock_logger.error.assert_called()

    @patch(f"{MOCK_SERVICE_PATH}.base64_url_encode")
    @patch(MOCK_LOGGER_PATH)
    def test_reimport_exception_continues(
        self,
        mock_logger: MagicMock,
        mock_b64_encode: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should log error and continue when exception occurs during reimport."""
        queries = [
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT 1",
                xnat_project_id=uuid4(),
                last_reimport=None,
                trust_id=uuid4(),
                trust_name="T1",
            ),
        ]
        mock_b64_encode.side_effect = lambda q: "encoded"
        mock_db_session.add.side_effect = Exception("DB error")

        result = reimport_failed_studies(queries, mock_db_session, 0)

        assert result is False
        mock_logger.error.assert_called()


class TestGetImagingProjectsEdgeCases:
    @patch(MOCK_LOGGER_PATH)
    def test_generic_exception(self, mock_logger: MagicMock, mock_db_session: MagicMock):
        """Should raise on non-SQLAlchemy exceptions."""
        mock_db_session.exec.side_effect = RuntimeError("Unexpected")
        with pytest.raises(RuntimeError, match="Unexpected"):
            get_imaging_projects(uuid4(), mock_db_session)

    @patch(MOCK_LOGGER_PATH)
    def test_sqlalchemy_error(self, mock_logger: MagicMock, mock_db_session: MagicMock):
        """Should raise on SQLAlchemy errors."""
        mock_db_session.exec.side_effect = SQLAlchemyError("DB error")
        with pytest.raises(SQLAlchemyError):
            get_imaging_projects(uuid4(), mock_db_session)


class TestGetXnatProjectStatusInfoEdgeCases:
    @patch(MOCK_LOGGER_PATH)
    def test_sqlalchemy_error(self, mock_logger: MagicMock, mock_db_session: MagicMock):
        """Should raise on SQLAlchemy errors."""
        mock_db_session.exec.side_effect = SQLAlchemyError("DB error")
        with pytest.raises(SQLAlchemyError):
            get_xnat_project_status_info(uuid4(), mock_db_session)


class TestUpdateXnatUserProfileEdgeCases:
    @patch(MOCK_GET_TRUSTS_PATH)
    @patch(MOCK_LOGGER_PATH)
    def test_no_trusts_found(
        self,
        mock_logger: MagicMock,
        mock_get_trusts: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should log error and return when no trusts found."""
        mock_get_trusts.return_value = []
        request_data = IUpdateXnatProfile(email="user@example.com", enabled=True)

        update_xnat_user_profile(request_data, mock_db_session)

        mock_logger.error.assert_called_once()
        mock_db_session.add.assert_not_called()


class TestGetLatestImagingStatus:
    def test_returns_parsed_status(self, mock_db_session: MagicMock):
        """Should parse import status from completed task result."""
        trust_id = uuid4()
        xnat_project_id = uuid4()
        mock_task = MagicMock()
        mock_task.result = json.dumps({
            "import_status": {
                "successful_count": 10,
                "failed_count": 2,
                "processing_count": 3,
                "queued_count": 5,
                "queue_failed_count": 1,
            }
        })
        mock_db_session.exec.return_value.first.return_value = mock_task

        result = _get_latest_imaging_status(trust_id, xnat_project_id, mock_db_session)

        assert result is not None
        assert result.successful_count == 10
        assert result.failed_count == 2
        assert result.processing_count == 3
        assert result.queued_count == 5
        assert result.queue_failed_count == 1

    def test_returns_none_when_no_task(self, mock_db_session: MagicMock):
        """Should return None when no completed task exists."""
        mock_db_session.exec.return_value.first.return_value = None

        result = _get_latest_imaging_status(uuid4(), uuid4(), mock_db_session)

        assert result is None

    def test_returns_none_when_no_result_data(self, mock_db_session: MagicMock):
        """Should return None when task has no result."""
        mock_task = MagicMock()
        mock_task.result = None
        mock_db_session.exec.return_value.first.return_value = mock_task

        result = _get_latest_imaging_status(uuid4(), uuid4(), mock_db_session)

        assert result is None

    def test_handles_malformed_json(self, mock_db_session: MagicMock):
        """Should return None and not raise on malformed result JSON."""
        mock_task = MagicMock()
        mock_task.result = "not valid json"
        mock_db_session.exec.return_value.first.return_value = mock_task

        result = _get_latest_imaging_status(uuid4(), uuid4(), mock_db_session)

        assert result is None

    def test_handles_flat_result_structure(self, mock_db_session: MagicMock):
        """Should handle result where counts are at the top level (no import_status wrapper)."""
        mock_task = MagicMock()
        mock_task.result = json.dumps({
            "successful_count": 5,
            "failed_count": 0,
            "processing_count": 1,
            "queued_count": 2,
            "queue_failed_count": 0,
        })
        mock_db_session.exec.return_value.first.return_value = mock_task

        result = _get_latest_imaging_status(uuid4(), uuid4(), mock_db_session)

        assert result is not None
        assert result.successful_count == 5


class TestHasPendingImagingTasks:
    def test_returns_true_when_pending_task_exists(self, mock_db_session: MagicMock):
        """Should return True when a PENDING CREATE_IMAGING task matches the project."""
        project_id = uuid4()
        mock_task = MagicMock()
        mock_task.payload = json.dumps({"project_id": str(project_id), "trust_id": str(uuid4())})
        mock_db_session.exec.return_value.all.return_value = [mock_task]

        assert has_pending_imaging_tasks(project_id, mock_db_session) is True

    def test_returns_true_when_in_progress_task_exists(self, mock_db_session: MagicMock):
        """Should return True when an IN_PROGRESS CREATE_IMAGING task matches the project."""
        project_id = uuid4()
        mock_task = MagicMock()
        mock_task.payload = json.dumps({"project_id": str(project_id)})
        mock_db_session.exec.return_value.all.return_value = [mock_task]

        assert has_pending_imaging_tasks(project_id, mock_db_session) is True

    def test_returns_false_when_no_tasks(self, mock_db_session: MagicMock):
        """Should return False when no pending/in-progress CREATE_IMAGING tasks exist."""
        mock_db_session.exec.return_value.all.return_value = []

        assert has_pending_imaging_tasks(uuid4(), mock_db_session) is False

    def test_returns_false_when_tasks_for_different_project(self, mock_db_session: MagicMock):
        """Should return False when tasks exist but for a different project."""
        project_id = uuid4()
        other_project_id = uuid4()
        mock_task = MagicMock()
        mock_task.payload = json.dumps({"project_id": str(other_project_id)})
        mock_db_session.exec.return_value.all.return_value = [mock_task]

        assert has_pending_imaging_tasks(project_id, mock_db_session) is False

    def test_handles_malformed_payload(self, mock_db_session: MagicMock):
        """Should return False when task payload is malformed JSON."""
        mock_task = MagicMock()
        mock_task.payload = "not valid json"
        mock_db_session.exec.return_value.all.return_value = [mock_task]

        assert has_pending_imaging_tasks(uuid4(), mock_db_session) is False


class TestGetImagingProjectStatusesEdgeCases:
    @patch(f"{MOCK_SERVICE_PATH}.get_xnat_project_status_info")
    @patch(MOCK_LOGGER_PATH)
    def test_empty_list(
        self,
        mock_logger: MagicMock,
        mock_get_xnat_status: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should log error when no imaging projects provided."""
        results = get_imaging_project_statuses([], "encoded", mock_db_session)

        assert results == []
        mock_logger.error.assert_called_once()


class TestBase64UrlEncode:
    def test_encodes_string(self):
        """Should produce correct base64url encoding."""
        result = base64_url_encode("hello world")
        expected = base64.urlsafe_b64encode(b"hello world").decode("utf-8").rstrip("=")
        assert result == expected

    def test_strips_padding(self):
        """Should strip '=' padding characters from output."""
        result = base64_url_encode("a")
        assert "=" not in result


class TestReimportFailedStudiesRateLimited:
    @patch(MOCK_LOGGER_PATH)
    def test_all_queries_rate_limited(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
    ):
        """Should return True and queue no tasks when all queries are within the rate limit."""
        queries = [
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT 1",
                xnat_project_id=uuid4(),
                last_reimport=datetime.now(timezone.utc) - timedelta(minutes=5),
                trust_id=uuid4(),
                trust_name="T1",
            ),
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT 2",
                xnat_project_id=uuid4(),
                last_reimport=datetime.now(timezone.utc) - timedelta(minutes=10),
                trust_id=uuid4(),
                trust_name="T2",
            ),
        ]

        result = reimport_failed_studies(queries, mock_db_session, project_reimport_rate_minutes=60)

        assert result is True
        mock_db_session.add.assert_not_called()
        mock_db_session.commit.assert_called_once()
        mock_logger.info.assert_any_call("No queries were eligible for reimport at this time.")
