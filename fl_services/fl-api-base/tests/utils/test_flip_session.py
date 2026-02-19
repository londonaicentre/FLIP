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

from pathlib import Path
from typing import NamedTuple
from unittest.mock import MagicMock, patch

import pytest
from fl_api.utils.flip_session import new_secure_Flip_session
from nvflare.apis.fl_constant import AdminCommandNames
from nvflare.fuel.flare_api.api_spec import JobNotFound, NoConnection
from nvflare.fuel.hci.client.api import ResultKey
from nvflare.fuel.hci.client.api_status import APIStatus
from nvflare.fuel.hci.client.fl_admin_api import TargetType
from nvflare.fuel.hci.client.fl_admin_api_spec import APISyntaxError, FLAdminAPIResponse
from nvflare.fuel.hci.proto import MetaKey

MODULE_DIR = Path(__file__).parents[2]
FL_ADMIN_DIR = f"{MODULE_DIR}/admin"


@pytest.fixture(autouse=True)
def fed_admin_json(tmp_path):
    """
    Autouse fixture:
    - Creates a temporary test fed_admin.json file for testing
    - Uses tmp_path for isolation
    """
    # Use tmp_path for creating temporary test directories
    admin_dir = tmp_path / "admin" / "startup"
    admin_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = admin_dir / "fed_admin.json"

    # Create a minimal fed_admin.json file for testing
    cfg_path.write_text('{"admin": {}}')

    # Temporarily override the MODULE_DIR to use tmp_path
    import fl_api.utils.flip_session as flip_session_module

    original_admin_dir = getattr(flip_session_module, "FL_ADMIN_DIRECTORY", None)

    yield cfg_path

    # Restore original if needed (though tmp_path will be cleaned up automatically)
    if original_admin_dir:
        setattr(flip_session_module, "FL_ADMIN_DIRECTORY", original_admin_dir)


@pytest.fixture
def fake_success_response():
    """Fixture to create a fake response object."""
    response = NamedTuple(
        "FakeResponse",
        [("status", APIStatus), ("details", dict)],
    )(
        status=APIStatus.SUCCESS,
        details={"message": "Client removed successfully"},
    )
    return (
        True,
        response,
        "test_remove_client_success",
    )


@pytest.fixture
def fake_error_response():
    """Fixture to create a fake error response object."""
    response = NamedTuple(
        "FakeResponse",
        [("status", APIStatus), ("details", dict)],
    )(
        status=APIStatus.ERROR_RUNTIME,
        details={"message": "Invalid targets: invalid_target"},
    )
    return (
        True,
        response,
        "test_remove_client_invalid_targets",
    )


@pytest.fixture
@patch("nvflare.fuel.flare_api.flare_api.Session.__init__")
@patch("fl_api.utils.flip_session.FLIP_Session.try_connect")
def session(mock_connection, mock_session_init):
    # Mock the Session parent class initialization to avoid config loading
    mock_session_init.return_value = None
    mock_connection.return_value = True

    session = new_secure_Flip_session(
        username="admin",
        startup_kit_location=FL_ADMIN_DIR,
        timeout=0.1,
    )
    # Set up any attributes that tests might need
    session._error_buffer = None
    return session


class TestValidateRequiredTargetString:
    """Unit tests for FLIP_Session._validate_required_target_string"""

    @pytest.fixture(autouse=True)
    def _setup(self, session):
        self.session = session

    @pytest.mark.parametrize(
        "target",
        [
            "client1",
            "client-123",
            "client_ABC",
            "Client.Name",
            "CLIENT-1_2.3",
        ],
    )
    def test_valid_target_strings(self, target):
        """✅ Valid target strings should pass without error."""
        result = self.session._validate_required_target_string(target)
        assert result == target

    @pytest.mark.parametrize(
        ("target", "expected_msg"),
        [
            ("", "target is required but not specified."),
            (None, "target is required but not specified."),
        ],
    )
    def test_missing_or_empty_target(self, target, expected_msg):
        """🚫 Missing or empty targets should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match=expected_msg):
            self.session._validate_required_target_string(target)

    @pytest.mark.parametrize(
        ("target", "expected_msg"),
        [
            (123, "target is not str."),
            (["client1"], "target is not str."),
            ({"name": "client1"}, "target is not str."),
        ],
    )
    def test_non_string_targets(self, target, expected_msg):
        """🚫 Non-string target types should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match=expected_msg):
            self.session._validate_required_target_string(target)

    @pytest.mark.parametrize(
        "target",
        [
            "client name",  # space not allowed
            "client@name",  # invalid symbol
            "client$name",  # invalid symbol
            "client#name",  # invalid symbol
            "name*",  # invalid symbol
        ],
    )
    def test_invalid_characters_in_target(self, target):
        """🚫 Targets with invalid characters should raise APISyntaxError."""
        with pytest.raises(
            APISyntaxError,
            match="target must be a string of only valid characters and no spaces.",
        ):
            self.session._validate_required_target_string(target)


class TestValidateFileString:
    """Unit tests for FLIP_Session._validate_file_string"""

    @pytest.fixture(autouse=True)
    def _setup(self, session):
        self.session = session

    @pytest.mark.parametrize(
        "file_path",
        [
            "file.txt",
            "subdir/file.txt",
            "dir1/dir2/file-123_ABC.log",
            "some.file_name.csv",
        ],
    )
    def test_valid_file_strings(self, file_path):
        """✅ Valid file paths should pass validation."""
        with patch("fl_api.utils.flip_session.validate_text_file_name", return_value=None):
            result = self.session._validate_file_string(file_path)
            assert result == file_path

    @pytest.mark.parametrize(
        ("file_value", "expected_msg"),
        [
            (123, "file is not str."),
            (["abc.txt"], "file is not str."),
            (None, "file is not str."),
        ],
    )
    def test_non_string_file_values(self, file_value, expected_msg):
        """🚫 Non-string file names should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match=expected_msg):
            self.session._validate_file_string(file_value)

    @pytest.mark.parametrize(
        "file_path",
        [
            "file name.txt",  # contains space
            "file@name.txt",  # invalid char
            "file$name.txt",  # invalid char
            "file#name.txt",  # invalid char
            "file|name.txt",  # invalid char
        ],
    )
    def test_invalid_characters_in_file_name(self, file_path):
        """🚫 Invalid characters should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match="unsupported characters in file"):
            self.session._validate_file_string(file_path)

    def test_absolute_path_raises_error(self):
        """🚫 Absolute paths should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match="absolute path for file is not allowed"):
            self.session._validate_file_string("/etc/passwd")

    @pytest.mark.parametrize(
        "file_path",
        [
            "dir1/../file.txt",
            "../file.txt",
            "nested/../../etc/passwd",
        ],
    )
    def test_relative_parent_dir_not_allowed(self, file_path):
        """🚫 '..' segments in path should raise APISyntaxError."""
        with pytest.raises(APISyntaxError, match=r"\.\. in file path is not allowed"):
            self.session._validate_file_string(file_path)

    def test_validate_text_file_name_returns_error(self):
        """🚫 If validate_text_file_name returns an error string, raise APISyntaxError."""
        with patch("fl_api.utils.flip_session.validate_text_file_name", return_value="invalid file extension"):
            with pytest.raises(APISyntaxError, match="invalid file extension"):
                self.session._validate_file_string("badfile.tmp")

    def test_validate_text_file_name_returns_none(self):
        """✅ If validate_text_file_name returns None, validation passes."""
        with patch("fl_api.utils.flip_session.validate_text_file_name", return_value=None):
            result = self.session._validate_file_string("goodfile.txt")
            assert result == "goodfile.txt"


class TestValidateSPString:
    """Unit tests for FLIP_Session._validate_sp_string"""

    @pytest.fixture(autouse=True)
    def _setup(self, session):
        """Use shared session fixture."""
        self.session = session

    @pytest.mark.parametrize(
        "sp_string",
        [
            "example.com:8002:8003",
            "localhost:1234:5678",
            "test-server.local:9000:9001",
            "mydomain.io:5000:5001",
        ],
    )
    def test_valid_sp_string(self, sp_string):
        """✅ Valid SP endpoint strings should pass validation."""
        result = self.session._validate_sp_string(sp_string)
        assert result == sp_string

    @pytest.mark.parametrize(
        "sp_string",
        [
            "example.com",  # missing ports
            "example.com:8002",  # only one port
            "example.com:abcd:8003",  # invalid port format
            "example@:8002:8003",  # invalid character in hostname
            "example.com:8002:8003:8004",  # too many sections
            ":8002:8003",  # missing hostname
            "",  # empty string
        ],
    )
    def test_invalid_sp_string(self, sp_string):
        """🚫 Invalid SP endpoint strings should raise APISyntaxError."""
        with pytest.raises(
            APISyntaxError,
            match="sp_string must be of the format example.com:8002:8003",
        ):
            self.session._validate_sp_string(sp_string)

    def test_none_sp_string_raises_typeerror(self):
        """🚫 None should raise a TypeError because re.match expects a string."""
        with pytest.raises(TypeError, match="expected string or bytes-like object"):
            self.session._validate_sp_string(None)


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id")
@patch("fl_api.utils.flip_session.FLIP_Session._do_command")
def test_download_job_result_success(mock_do_command, mock_validate_job_id, session):
    """✅ Should call _do_command and return the location when successful."""
    mock_validate_job_id.return_value = None
    mock_do_command.return_value = {ResultKey.META: {MetaKey.LOCATION: "/tmp/job_results/job_123"}}

    result = session.download_job_result("job_123")

    mock_validate_job_id.assert_called_once_with("job_123")
    mock_do_command.assert_called_once_with(f"{AdminCommandNames.DOWNLOAD_JOB} job_123")
    assert result == "/tmp/job_results/job_123"


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id", side_effect=JobNotFound("job_999"))
def test_download_job_result_job_not_found(mock_validate_job_id, session):
    """🚫 Should raise JobNotFound if job ID validation fails."""
    with pytest.raises(JobNotFound, match="job_999"):
        session.download_job_result("job_999")


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id")
@patch("fl_api.utils.flip_session.FLIP_Session._collect_info")
def test_reset_errors_calls_collect_info(mock_collect_info, mock_validate_job_id, session):
    """✅ Should call _collect_info with correct args."""
    mock_validate_job_id.return_value = None
    session.reset_errors("job_abc")

    mock_validate_job_id.assert_called_once_with("job_abc")
    mock_collect_info.assert_called_once_with(AdminCommandNames.RESET_ERRORS, "job_abc", TargetType.ALL)


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id", side_effect=JobNotFound("job_xyz"))
def test_reset_errors_job_not_found(mock_validate_job_id, session):
    """🚫 Should raise JobNotFound if job ID is invalid."""
    with pytest.raises(JobNotFound, match="job_xyz"):
        session.reset_errors("job_xyz")


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id")
@patch("fl_api.utils.flip_session.FLIP_Session._do_command")
def test_abort_job_calls_do_command(mock_do_command, mock_validate_job_id, session):
    """✅ Should call _do_command with correct abort command."""
    mock_validate_job_id.return_value = None
    session.abort_job("job_111")

    mock_validate_job_id.assert_called_once_with("job_111")
    mock_do_command.assert_called_once_with(f"{AdminCommandNames.ABORT_JOB} job_111")


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id", side_effect=JobNotFound("job_222"))
def test_abort_job_job_not_found(mock_validate_job_id, session):
    """🚫 Should raise JobNotFound when _validate_job_id fails."""
    with pytest.raises(JobNotFound, match="job_222"):
        session.abort_job("job_222")


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id")
@patch("fl_api.utils.flip_session.FLIP_Session._do_command")
def test_delete_job_calls_do_command(mock_do_command, mock_validate_job_id, session):
    """✅ Should call _do_command with correct delete command."""
    mock_validate_job_id.return_value = None
    session.delete_job("job_321")

    mock_validate_job_id.assert_called_once_with("job_321")
    mock_do_command.assert_called_once_with(f"{AdminCommandNames.DELETE_JOB} job_321")


@patch("fl_api.utils.flip_session.FLIP_Session._validate_job_id", side_effect=JobNotFound("job_404"))
def test_delete_job_job_not_found(mock_validate_job_id, session):
    """🚫 Should raise JobNotFound when validation fails."""
    with pytest.raises(JobNotFound, match="job_404"):
        session.delete_job("job_404")


@patch("nvflare.fuel.flare_api.flare_api.Session.__init__")
@patch("fl_api.utils.flip_session.FLIP_Session.try_connect")
def test_wait_until_client_status_timeout(mock_connection, mock_session_init):
    """Should raise NoConnection when connection times out."""
    mock_session_init.return_value = None
    mock_connection.side_effect = NoConnection("cannot connect to FLARE in 0.1 seconds")

    with pytest.raises(NoConnection, match="cannot connect to FLARE in 0.1 seconds"):
        new_secure_Flip_session(
            username="admin",
            startup_kit_location=FL_ADMIN_DIR,
            timeout=0.1,
        )


def _get_status(response):
    """Helper to safely extract the APIStatus from an FLAdminAPIResponse or dict."""
    if isinstance(response, dict):
        return response.get("status")
    return getattr(response, "status", None)


@patch("fl_api.utils.flip_session.time.sleep", return_value=None)
def test_wait_until_server_status_success(mock_sleep, session):
    """✅ Should return SUCCESS when callback condition is met."""
    fake_reply = MagicMock()
    fake_reply.status = True

    with patch.object(session, "check_server_status", return_value=fake_reply):
        mock_callback = MagicMock(return_value=True)
        response = session.wait_until_server_status(
            interval=0.01,
            timeout=1,
            callback=mock_callback,
        )

    mock_callback.assert_called_once_with(fake_reply)
    assert isinstance(response, (FLAdminAPIResponse, dict))
    assert _get_status(response) == APIStatus.SUCCESS


@patch("fl_api.utils.flip_session.time.sleep", return_value=None)
def test_wait_until_server_status_timeout(mock_sleep, session):
    """✅ Should return SUCCESS with timeout message if condition not met."""
    fake_reply = MagicMock()
    fake_reply.status = True

    with (
        patch.object(session, "check_server_status", return_value=fake_reply),
        patch("fl_api.utils.flip_session.time.time", side_effect=[0, 2, 4, 6]),
    ):
        mock_callback = MagicMock(return_value=False)
        response = session.wait_until_server_status(
            interval=0.01,
            timeout=3,
            callback=mock_callback,
        )

    assert isinstance(response, (FLAdminAPIResponse, dict))
    assert _get_status(response) == APIStatus.SUCCESS
    assert response["details"]["message"] == "Waited until timeout."
    assert mock_callback.call_count >= 1


@patch("fl_api.utils.flip_session.time.sleep", return_value=None)
def test_wait_until_server_status_fail_attempts(mock_sleep, session):
    """🚫 Should return ERROR_RUNTIME after exceeding fail_attempts."""
    fake_reply = MagicMock()
    fake_reply.status = False

    with patch.object(session, "check_server_status", return_value=fake_reply):
        response = session.wait_until_server_status(
            interval=0.01,
            timeout=None,
            fail_attempts=2,
            callback=lambda x: False,
        )

    assert isinstance(response, (FLAdminAPIResponse, dict))
    assert _get_status(response) == APIStatus.ERROR_RUNTIME
    assert "fail_attempts" in response["details"]["message"]


@patch("fl_api.utils.flip_session.time.sleep", return_value=None)
def test_wait_until_server_status_check_raises_exception(mock_sleep, session):
    """🚫 Should return ERROR_RUNTIME if check_server_status raises."""
    with patch.object(session, "check_server_status", side_effect=Exception("boom")):
        response = session.wait_until_server_status(
            interval=0.01,
            timeout=1,
        )

    assert isinstance(response, (FLAdminAPIResponse, dict))
    assert _get_status(response) == APIStatus.ERROR_RUNTIME
    assert "Failed to check server status" in response["details"]["message"]
