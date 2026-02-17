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

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from nvflare.fuel.flare_api.api_spec import InternalError

from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import ServerInfoModel, SystemInfoModel

FL_ADMIN_DIR = str(Path(__file__).parents[2] / "admin")


@pytest.fixture
@patch("nvflare.fuel.flare_api.flare_api.Session.__init__")
def session(mock_session_init):
    """
    Create a FLIP_Session without invoking real NVFlare Session initialization.
    """
    mock_session_init.return_value = None
    return FLIP_Session(username="u", startup_path="p", secure_mode=False, debug=False)


def test_do_command_retries_once_on_session_inactive(session):
    """✅ Reconnect + retry once when parent _do_command raises session_inactive."""
    with (
        patch(
            "nvflare.fuel.flare_api.flare_api.Session._do_command",
            side_effect=[InternalError("session_inactive"), {"ok": True}],
        ) as parent_do_command,
        patch.object(session, "try_connect") as try_connect,
    ):
        result = session._do_command("CMD")

    assert result == {"ok": True}
    try_connect.assert_called_once_with(timeout=5.0)
    assert parent_do_command.call_count == 2
    parent_do_command.assert_has_calls([call("CMD"), call("CMD")])


def test_check_server_status_returns_server_info(session):
    sys_info = SystemInfoModel(
        server_info=ServerInfoModel(status="running", start_time=123.0),
        client_info=[],
        job_info=[],
    )

    with patch.object(session, "get_system_info", return_value=sys_info):
        out = session.check_server_status()

    assert isinstance(out, ServerInfoModel)
    assert out.status == "running"
    assert out.start_time == 123.0


def test_check_client_status_all_clients(session):
    sys_info = SimpleNamespace(
        client_info=[
            SimpleNamespace(name="c1", last_connect_time=1700000001.0),
            SimpleNamespace(name="c2", last_connect_time=1700000002.0),
        ]
    )

    def job_status_side_effect(names):
        assert len(names) == 1
        name = names[0]
        return [{"status": "online" if name == "c1" else "offline"}]

    with (
        patch.object(session, "get_system_info", return_value=sys_info),
        patch.object(session, "get_client_job_status", side_effect=job_status_side_effect),
    ):
        out = session.check_client_status()

    assert [c.name for c in out] == ["c1", "c2"]
    assert [c.last_connect_time for c in out] == [1700000001.0, 1700000002.0]
    assert [c.status for c in out] == ["online", "offline"]


def test_check_client_status_filters_target(session):
    sys_info = SimpleNamespace(
        client_info=[
            SimpleNamespace(name="c1", last_connect_time=1.0),
            SimpleNamespace(name="c2", last_connect_time=2.0),
            SimpleNamespace(name="c3", last_connect_time=3.0),
        ]
    )

    with (
        patch.object(session, "get_system_info", return_value=sys_info),
        patch.object(session, "get_client_job_status", return_value=[{"status": "online"}]) as get_client_job_status,
    ):
        out = session.check_client_status(target=["c2", "c3"])

    assert [c.name for c in out] == ["c2", "c3"]
    assert get_client_job_status.call_args_list == [call(["c2"]), call(["c3"])]


def test_check_client_status_uses_unknown_when_status_missing(session):
    sys_info = SimpleNamespace(client_info=[SimpleNamespace(name="c1", last_connect_time=1.0)])

    with (
        patch.object(session, "get_system_info", return_value=sys_info),
        patch.object(session, "get_client_job_status", return_value=[{}]),
    ):
        out = session.check_client_status()

    assert len(out) == 1
    assert out[0].name == "c1"
    assert out[0].status == "unknown"


def test_check_client_status_asserts_when_multiple_job_statuses_returned(session):
    sys_info = SimpleNamespace(client_info=[SimpleNamespace(name="c1", last_connect_time=1.0)])

    with (
        patch.object(session, "get_system_info", return_value=sys_info),
        patch.object(session, "get_client_job_status", return_value=[{"status": "a"}, {"status": "b"}]),
    ):
        with pytest.raises(AssertionError, match="Expected only one job status for client c1"):
            session.check_client_status()
