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
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from nvflare.fuel.flare_api.api_spec import InternalError, NoConnection, SessionClosed

from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import ServerInfoModel, SystemInfoModel

FL_ADMIN_DIR = str(Path(__file__).parents[2] / "admin")


@pytest.fixture
@patch("nvflare.fuel.flare_api.flare_api.Session.__init__")
def session(mock_session_init):
    """
    Create a FLIP_Session without invoking real NVFlare Session initialization.

    Starts marked as already connected: session_inactive/SessionClosed retry paths can only be hit
    after a real connection existed, so that's the realistic starting point for testing them. Tests
    of the lazy first-connect path (FLIP#735) explicitly reset ``_connected`` to False.
    """
    mock_session_init.return_value = None
    session = FLIP_Session(username="u", startup_path="p", secure_mode=False, debug=False)
    session._connected = True
    return session


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


def test_do_command_retries_once_on_session_closed(session):
    """✅ Reconnect + retry once when parent _do_command raises SessionClosed."""
    sentinel = {"reconnected": True}
    with (
        patch(
            "nvflare.fuel.flare_api.flare_api.Session._do_command",
            side_effect=[SessionClosed("session closed"), sentinel],
        ) as parent_do_command,
        patch.object(session, "_reconnect") as mock_reconnect,
    ):
        result = session._do_command("CMD")

    assert result == sentinel
    mock_reconnect.assert_called_once_with()
    assert parent_do_command.call_count == 2
    parent_do_command.assert_has_calls([call("CMD"), call("CMD")])


def test_do_command_propagates_exception_if_retry_fails_after_reconnect(session):
    """❌ SessionClosed propagates when the retry after reconnect also raises."""
    with (
        patch(
            "nvflare.fuel.flare_api.flare_api.Session._do_command",
            side_effect=[SessionClosed("session closed"), SessionClosed("still closed")],
        ) as parent_do_command,
        patch.object(session, "_reconnect") as mock_reconnect,
    ):
        with pytest.raises(SessionClosed, match="still closed"):
            session._do_command("CMD")

    mock_reconnect.assert_called_once_with()
    assert parent_do_command.call_count == 2


def test_do_command_connects_lazily_when_never_connected(session):
    """✅ First _do_command call connects if the session was never successfully connected
    (PER_JOB_FL_SERVER let boot proceed with an unreachable fl-server, FLIP#735)."""
    session._connected = False
    with (
        patch("nvflare.fuel.flare_api.flare_api.Session._do_command", return_value={"ok": True}) as parent_do_command,
        patch.object(session, "try_connect") as try_connect,
    ):
        result = session._do_command("CMD")

    assert result == {"ok": True}
    try_connect.assert_called_once_with(timeout=5.0)
    parent_do_command.assert_called_once_with("CMD")


def test_do_command_skips_connect_when_already_connected(session):
    """✅ No extra connect attempt once the session has already connected successfully."""
    session._connected = True
    with (
        patch("nvflare.fuel.flare_api.flare_api.Session._do_command", return_value={"ok": True}),
        patch.object(session, "try_connect") as try_connect,
    ):
        result = session._do_command("CMD")

    assert result == {"ok": True}
    try_connect.assert_not_called()


def test_try_connect_marks_session_connected_on_success(session):
    """✅ try_connect flips _connected to True only after the parent call succeeds."""
    session._connected = False
    with patch("nvflare.fuel.flare_api.flare_api.Session.try_connect") as parent_try_connect:
        session.try_connect(5.0)

    parent_try_connect.assert_called_once_with(5.0)
    assert session._connected is True
    assert session.is_connected is True


def test_try_connect_does_not_mark_connected_on_failure(session):
    """✅ try_connect leaves _connected False when the parent call raises."""
    session._connected = False
    with patch("nvflare.fuel.flare_api.flare_api.Session.try_connect", side_effect=NoConnection("down")):
        with pytest.raises(NoConnection):
            session.try_connect(5.0)

    assert session._connected is False
    assert session.is_connected is False


def test_reconnect_resets_connected_flag_before_reinitializing(session):
    """✅ _reconnect marks _connected False up front, before re-init and reconnect, so a failed
    reconnect never leaves a stale True from the previous (now-closed) session."""
    session.username = "u"  # real Session.__init__ sets this; the fixture mocks it out
    with (
        patch("nvflare.fuel.flare_api.flare_api.Session.__init__", return_value=None) as mock_init,
        patch.object(session, "try_connect") as mock_try_connect,
    ):
        session._reconnect()

    mock_init.assert_called_once()
    mock_try_connect.assert_called_once_with(timeout=5.0)
    # try_connect is mocked out here, so nothing re-sets _connected to True in this test —
    # confirms _reconnect itself is what clears the flag, not a side effect of try_connect.
    assert session._connected is False


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
