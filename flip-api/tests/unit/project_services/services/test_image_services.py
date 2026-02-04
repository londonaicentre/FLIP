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
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from flip_api.domain.interfaces.project import (
    IImagingStatusResponse,
    IReimportQuery,
    IUpdateXnatProfile,
)
from flip.domain.interfaces.trust import ITrust
from flip.domain.schemas.projects import (
    ImagingProject,
    XnatProjectStatusInfo,
)
from flip.domain.schemas.status import XNATImageStatus
from flip.project_services.services.image_service import (
    delete_imaging_project,
    get_imaging_project_statuses,
    get_imaging_projects,
    get_xnat_project_status_info,
    reimport_failed_studies,
    update_xnat_user_profile,
)

# Mocking paths
MOCK_SERVICE_PATH = "flip.project_services.services.image_service"
MOCK_LOGGER_PATH = f"{MOCK_SERVICE_PATH}.logger"
# MOCK_API_REQUEST_PATH = f"{MOCK_SERVICE_PATH}.api_request_to_trust"  # Placeholder for actual path
MOCK_HTTP_REQUEST_PATH = f"{MOCK_SERVICE_PATH}.http_request"  # Placeholder for actual path
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
        endpoint="http://trust1.example.com",
        reimport_count=0,
    )


# @pytest.fixture
# def mocked_settings():
#     mock = Settings(
#         PRIVATE_API_KEY="test_api_key",
#     )
#     with patch(f"{MOCK_SERVICE_PATH}.get_settings", return_value=mock):
#         yield mock


# --- get_imaging_projects ---
class TestGetImagingProjects:
    def test_success(self, mock_db_session: MagicMock, sample_project_id: UUID):
        db_row_data = [
            (uuid4(), uuid4(), uuid4(), "CREATED", "Trust XNAT 1", "http://t1.com", 0),
            (uuid4(), uuid4(), uuid4(), "DELETED", "Trust XNAT 2", "http://t2.com", 1),
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
        assert result[0].endpoint == db_row_data[0][5]
        assert result[0].reimport_count == db_row_data[0][6]

        mock_db_session.exec.assert_called_once()
        # Further checks on the SQL query can be added if needed

    @patch(MOCK_LOGGER_PATH)
    def test_db_error(self, mock_logger: MagicMock, mock_db_session: MagicMock, sample_project_id: UUID):
        db_error = Exception("DB Read Error")
        mock_db_session.exec.side_effect = db_error
        with pytest.raises(Exception, match="DB Read Error"):
            get_imaging_projects(sample_project_id, mock_db_session)
        mock_logger.error.assert_called_once()


# --- delete_imaging_project ---
class TestDeleteImagingProject:
    # @patch(MOCK_API_REQUEST_PATH)
    @patch(MOCK_LOGGER_PATH)
    def test_success(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
        sample_imaging_project_data: ImagingProject,
    ):
        with patch("httpx.Client.delete") as mock_client:
            mock_client.return_value = MagicMock(status_code=200)
            result = delete_imaging_project(sample_imaging_project_data, mock_db_session)

        assert result is True
        expected_endpoint = (
            f"{sample_imaging_project_data.endpoint}/imaging/{sample_imaging_project_data.xnat_project_id}"
        )
        mock_client.assert_called_once_with(expected_endpoint)

        mock_db_session.execute.assert_called_once()
        # Check the update statement
        # Example: args, _ = mock_db_session.exec.call_args; str(args[0]) should contain UPDATE...
        mock_db_session.commit.assert_called_once()  # Assuming commit is part of this function now

    # @patch(MOCK_API_REQUEST_PATH)
    @patch(MOCK_LOGGER_PATH)
    def test_api_delete_fails(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
        sample_imaging_project_data: ImagingProject,
    ):
        api_error = Exception("API Delete Failed")

        with patch("httpx.Client.delete") as mock_client:
            mock_client.side_effect = api_error
            result = delete_imaging_project(sample_imaging_project_data, mock_db_session)

        assert result is False
        mock_logger.error.assert_called_with(
            f"Error deleting imaging project via API or updating DB: {api_error}", exc_info=True
        )
        mock_db_session.exec.assert_not_called()  # DB update should not happen if API fails
        mock_db_session.rollback.assert_called_once()

    # @patch(MOCK_API_REQUEST_PATH)
    @patch(MOCK_LOGGER_PATH)
    def test_db_update_fails(
        self,
        mock_logger: MagicMock,
        mock_db_session: MagicMock,
        sample_imaging_project_data: ImagingProject,
    ):
        db_update_error = Exception("DB Update Failed")
        mock_db_session.execute.side_effect = db_update_error  # Error on db.exec for update

        with patch("httpx.Client.delete") as mock_client:
            mock_client.return_value = MagicMock(status_code=200)
            result = delete_imaging_project(sample_imaging_project_data, mock_db_session)

        assert result is False
        mock_logger.error.assert_called_with(
            f"Error deleting imaging project via API or updating DB: {db_update_error}", exc_info=True
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


# --- get_imaging_project_statuses (Simplified test due to complexity) ---
class TestGetImagingProjectStatuses:
    @patch(f"{MOCK_SERVICE_PATH}.get_xnat_project_status_info")
    @patch(MOCK_LOGGER_PATH)
    def test_partial_success(
        self,
        mock_logger: MagicMock,
        mock_get_xnat_status: MagicMock,
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
                endpoint="http://t1.com",
                reimport_count=0,
            ),
            ImagingProject(
                id=uuid4(),
                xnat_project_id=uuid4(),
                trust_id=trust_id_2,
                retrieve_image_status=XNATImageStatus.CREATED,
                name="Trust2",
                endpoint="http://t2.com",
                reimport_count=1,
            ),
        ]
        encoded_query = "ZXF1ZXJ5"  # "query"

        # Mock get_xnat_project_status_info
        mock_get_xnat_status.side_effect = [
            XnatProjectStatusInfo(retrieve_image_status=XNATImageStatus.CREATED, reimport_count=0),
            XnatProjectStatusInfo(retrieve_image_status=XNATImageStatus.RETRIEVE_COMPLETED, reimport_count=1),
        ]

        # Mock apiRequestToTrust.get
        # First call success, second fails
        mock_trust_1_response = MagicMock()
        mock_trust_1_response.status_code = 200
        mock_trust_1_response.json.return_value = IImagingStatusResponse(
            project_creation_completed=True,
            import_status=None,
        )  # type: ignore[call-arg]

        with patch("httpx.Client.get") as mock_client:
            mock_client.side_effect = [mock_trust_1_response, MagicMock(status_code=404)]
            results = get_imaging_project_statuses(imaging_projects_list, encoded_query, mock_db_session)

        assert len(results) == 2
        assert results[0].trust_id == trust_id_1
        assert results[0].project_creation_completed is True
        assert results[1].trust_id == trust_id_2
        # For the failed one, importStatus might be None or the object partially filled
        assert results[1].project_creation_completed is False  # Based on RETRIEVE_COMPLETED != CREATED

        assert mock_get_xnat_status.call_count == 2
        assert mock_client.call_count == 2
        mock_logger.error.assert_called_once()


# --- update_xnat_user_profile ---


class TestUpdateXnatUserProfile:
    @patch(MOCK_GET_TRUSTS_PATH)  # Mock the function that gets all trusts
    @patch(MOCK_LOGGER_PATH)
    def test_success_and_failure_mix(
        self,
        mock_logger: MagicMock,
        mock_get_trusts: MagicMock,
        mock_db_session: MagicMock,
    ):
        trust_list = [
            ITrust(id=uuid4(), name="Trust1", endpoint="http://t1.com"),
            ITrust(id=uuid4(), name="Trust2", endpoint="http://t2.com"),
        ]
        mock_get_trusts.return_value = trust_list

        request_data = IUpdateXnatProfile(email="user@example.com", enabled=True)

        # Simulate one success, one failure
        mock_http_response_success = MagicMock()
        mock_http_response_success.status_code = 200
        mock_http_response_success.text = "OK"
        mock_http_response_success.json.return_value = {"message": "updated"}

        with patch("httpx.Client.put") as mock_client:
            mock_client.side_effect = [mock_http_response_success, Exception("Update failed for Trust2")]
            update_xnat_user_profile(request_data, mock_db_session)

        assert mock_get_trusts.call_count == 1  # Called once with db session
        assert mock_client.call_count == 2

        # Check calls to api_request_to_trust.put
        calls = mock_client.call_args_list
        assert calls[0][0][0] == "http://t1.com/imaging/users"
        assert calls[0][1]["json"] == request_data.model_dump(mode="json")
        assert calls[1][0][0] == "http://t2.com/imaging/users"

        mock_logger.error.assert_called_with(
            "Unable to update XNAT user profile 'user@example.com' at Trust2 | Error: Update failed for Trust2"
        )
        mock_logger.info.assert_called_once()  # Called if any responses


# --- reimport_failed_studies ---
class TestReimportFailedStudies:
    @patch(f"{MOCK_SERVICE_PATH}.base64_url_encode")  # Mock helper
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
                last_reimport=datetime.utcnow() - timedelta(minutes=30),
                trust_id=uuid4(),
                trust_endpoint="http://t1.com",
                trust_name="T1",
            ),
            # This one should run because last_reimport is outside the rate limit
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT * FROM studies_2",
                xnat_project_id=uuid4(),
                last_reimport=datetime.utcnow() - timedelta(minutes=90),
                trust_id=uuid4(),
                trust_endpoint="http://t2.com",
                trust_name="T2",
            ),
            # No last reimport, so it should run
            IReimportQuery(
                query_id=uuid4(),
                query="SELECT * FROM studies_3",
                xnat_project_id=uuid4(),
                last_reimport=None,
                trust_id=uuid4(),
                trust_endpoint="http://t3.com",
                trust_name="T3",
            ),
        ]

        mock_b64_encode.side_effect = lambda q: base64.urlsafe_b64encode(q.encode()).decode()

        # Simulate HTTP responses
        mock_http_response_ok = MagicMock(status=200)
        mock_http_response_fail = MagicMock(status=500)

        with patch("httpx.Client.put") as mock_client:
            mock_client.side_effect = [
                mock_http_response_ok,  # For Q1
                mock_http_response_fail,  # For Q3 (will cause overall False)
            ]
            result = reimport_failed_studies(queries, mock_db_session, project_reimport_rate_minutes)

        assert result is False
        assert mock_b64_encode.call_count == 3
        assert mock_client.call_count == 2

        # Check the call for Q2
        first_call_args = mock_client.call_args_list[0]
        # Get the positional and keyword arguments
        args, kwargs = first_call_args
        assert "http://t2.com/imaging/" in args[0]
        # assert kwargs["api_key"] == mocked_settings.PRIVATE_API_KEY

        # Check the call for Q3
        second_call_args = mock_client.call_args_list[1]
        # Get the positional and keyword arguments
        args, kwargs = second_call_args
        assert "http://t3.com/imaging/" in args[0]
        # assert kwargs["api_key"] == mocked_settings.PRIVATE_API_KEY

        assert mock_db_session.commit.call_count == 0  # For Q1's successful update
        assert mock_db_session.rollback.call_count == 0  # No rollbacks for individual http errors in this design
