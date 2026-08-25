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

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from nvflare.fuel.flare_api.api_spec import InternalError, SessionClosed
from nvflare.fuel.flare_api.flare_api import Session

from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.schemas import ClientInfoModel, ServerInfoModel, SystemInfoModel, TargetType

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


# --------------------------------------------------------------------------------------
# Liskov guards (FLIP#1032)
#
# FLIP_Session is substituted for nvflare's Session, so NVFLARE's own code calls these
# methods. Two things must therefore hold, and neither is enforced by the language:
#   1. no override may narrow its base signature -- a parameter the base accepts and we do
#      not is a TypeError at whichever upstream call site passes it;
#   2. the models returned by the type-changing overrides must keep the attributes NVFLARE
#      reads off them.
# Both failed silently in the past, so they are pinned here rather than trusted.
# --------------------------------------------------------------------------------------


def _overridden_methods() -> list[str]:
    """Every FLIP_Session member that shadows one on the NVFLARE base.

    `__init__` is deliberately in scope -- the removed override narrowed it too -- while other
    dunders are not, since Python's own protocol methods are not part of this contract.
    """
    # getattr_static + isroutine rather than callable(): a `classmethod` or `property` object is
    # NOT callable, so a narrowed override of either kind would silently produce no test case.
    names = []
    for name in vars(FLIP_Session):
        if name.startswith("__") and name != "__init__":
            continue
        if not hasattr(Session, name):
            continue
        attr = inspect.getattr_static(FLIP_Session, name)
        target = attr.__func__ if isinstance(attr, (classmethod, staticmethod)) else attr
        if isinstance(attr, property):
            target = attr.fget
        if inspect.isroutine(target):
            names.append(name)
    return names


def test_there_are_overrides_to_check():
    """Guard the guard: a rename that empties the set must not silently pass the tests below."""
    assert set(_overridden_methods()) >= {"_do_command", "get_system_info", "get_connected_client_list"}


@pytest.mark.parametrize("method_name", _overridden_methods())
def test_override_does_not_narrow_the_base_signature(method_name):
    """Every parameter the base accepts must be accepted by our override.

    `_do_command` used to take only `cmd`, so NVFLARE's `_collect_info` calls
    (`enforce_meta=False`) -- show_errors, show_stats, reset_errors -- raised TypeError.
    """
    base_sig = inspect.signature(getattr(Session, method_name))
    override = inspect.signature(getattr(FLIP_Session, method_name))
    override_params = override.parameters
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in override_params.values())
    positional = [
        p
        for p in override_params.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    for index, (name, param) in enumerate(base_sig.parameters.items()):
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        # Every base parameter must be reachable BY NAME. `**kwargs` alone is not enough: a
        # renamed positional (the old `cmd` for the base's `command`) swallows the value
        # positionally but still breaks `_do_command(command=...)`. Named-and-in-the-same-slot
        # is the only shape that is truly substitutable.
        if name in override_params:
            slot_ok = name not in [p.name for p in positional] or positional[index].name == name
            assert slot_ok, (
                f"FLIP_Session.{method_name} accepts '{name}' but in a different position "
                f"({override}); positional callers would bind the wrong value."
            )
        else:
            # Absent by name is only acceptable when **kwargs can carry it AND the base gives it
            # a default -- a required base parameter must always be nameable.
            reachable = accepts_var_kw and param.default is not inspect.Parameter.empty
            assert reachable, (
                f"FLIP_Session.{method_name} does not accept base parameter '{name}' "
                f"({override}); narrowing a base signature breaks NVFLARE's own callers."
            )

        # A silently different default is the quieter half of the same bug: the removed
        # __init__ flipped secure_mode from the base's True to False, which no
        # parameter-presence check would notice.
        if name in override_params and param.default is not inspect.Parameter.empty:
            assert override_params[name].default == param.default, (
                f"FLIP_Session.{method_name} changes the default of '{name}' from "
                f"{param.default!r} to {override_params[name].default!r}; callers relying on the "
                f"base default silently get different behaviour."
            )


def test_do_command_forwards_base_keyword_arguments(session):
    """The regression itself: enforce_meta / props must reach the base untouched."""
    with patch("nvflare.fuel.flare_api.flare_api.Session._do_command", return_value={"ok": True}) as parent:
        session._do_command("SHOW_ERRORS job server", enforce_meta=False, props={"k": "v"})

    parent.assert_called_once_with("SHOW_ERRORS job server", enforce_meta=False, props={"k": "v"})


def test_do_command_forwards_keyword_arguments_through_a_reconnect(session):
    """A retry must re-issue the *same* command, kwargs included -- not a narrowed one."""
    with (
        patch(
            "nvflare.fuel.flare_api.flare_api.Session._do_command",
            side_effect=[SessionClosed("session closed"), {"ok": True}],
        ) as parent,
        patch.object(session, "_reconnect"),
    ):
        session._do_command("CMD", enforce_meta=False)

    assert parent.call_args_list == [call("CMD", enforce_meta=False), call("CMD", enforce_meta=False)]


def test_system_info_model_keeps_the_attributes_nvflare_reads():
    """Pin the duck-typed contract get_system_info's type swap rests on.

    NVFLARE's `_client_last_connect_times`, `_wait_for_clients_shutdown`,
    `_wait_for_clients_restart`, `restart` and `get_connected_client_list` all call
    `get_system_info()` and read these attributes off the result. Renaming any of them on
    FLIP's models breaks NVFLARE's restart and shutdown waits with an AttributeError deep
    inside upstream code.
    """
    info = SystemInfoModel(
        server_info=ServerInfoModel(status="running", start_time=123.0),
        client_info=[ClientInfoModel(name="c1", last_connect_time=1.0, status="online")],
        job_info=[],
    )

    assert info.server_info.status == "running"
    assert info.server_info.start_time == 123.0
    assert info.client_info[0].name == "c1"
    assert info.client_info[0].last_connect_time == 1.0


def test_reconnect_preserves_the_session_study(session):
    """Re-initialising without the study would silently change which jobs commands can see.

    Asserted by keyword, matching how `_reconnect` calls the base: a positional assertion would
    keep passing if NVFLARE reordered or inserted a parameter, which is the mis-binding the
    keyword call exists to prevent.
    """
    # The fixture's base __init__ is mocked, so none of these are set for real.
    session.username = "u"
    session.startup_path = "p"
    session.secure_mode = True
    session._debug = False
    session._study = "a-study"

    with (
        patch("nvflare.fuel.flare_api.flare_api.Session.__init__", return_value=None) as base_init,
        patch.object(session, "try_connect"),
    ):
        session._reconnect()

    base_init.assert_called_once_with(
        session, username="u", startup_path="p", secure_mode=True, debug=False, study="a-study"
    )


def test_construction_signature_matches_the_base():
    """There is no __init__ override, and if one is ever added it must not narrow the base.

    The removed override re-stashed username/startup_path/secure_mode under private aliases the
    base already keeps, while dropping the base's `study` parameter and flipping `secure_mode`
    from True to False (FLIP#1032). This asserts the resulting contract rather than the absence
    of an override, so a future override that genuinely forwards is still allowed.
    """
    params = inspect.signature(FLIP_Session.__init__).parameters
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    assert "study" in params or accepts_var_kw, "dropping `study` makes FLIP_Session(study=...) a TypeError"
    if "secure_mode" in params:
        assert params["secure_mode"].default is True, "secure_mode must default to the base's True"


@pytest.mark.parametrize(
    ("call_session", "expected_command"),
    [
        # The three `_collect_info` commands, in the argument shapes the routers actually pass:
        # `target_type` arrives as FLIP's TargetType StrEnum (schemas.py), and `targets` as a
        # comma-split list -- not the bare strings a hand-written test reaches for first.
        (lambda s: s.show_errors("job-1", TargetType.SERVER), "show_errors job-1 server"),
        (lambda s: s.show_errors("job-1", TargetType.CLIENT, ["site-1", "site-2"]),
         "show_errors job-1 client site-1 site-2"),
        (lambda s: s.show_stats("job-1", TargetType.SERVER), "show_stats job-1 server"),
        (lambda s: s.reset_errors("job-1"), "reset_errors job-1 all"),
        # The fourth affected route, reached through `_shell_command_on_target` rather than
        # `_collect_info` -- the site the original write-up missed entirely.
        (lambda s: s.get_working_directory("site-1"), "pwd site-1"),
    ],
)
def test_collect_info_commands_reach_the_transport(session, call_session, expected_command):
    """The end-to-end regression for FLIP#1032, through NVFLARE's own code.

NVFLARE 2.8.0 calls `_do_command(..., enforce_meta=False)` from eight sites; four of them are
    reachable from this service's routes -- show_errors / show_stats / reset_errors via
    `_collect_info`, and get_working_directory via `_shell_command_on_target`. Every one used to
    raise `TypeError: FLIP_Session._do_command() got an unexpected keyword argument
    'enforce_meta'` before reaching the wire.

    Only `session.api` is faked here, so the assertion covers the real `show_errors` ->
    `_collect_info` -> `FLIP_Session._do_command` -> `Session._do_command` chain rather than a
    mock of it.
    """
    session.api = SimpleNamespace(
        closed=False,
        do_command=lambda command, props=None: {
            "status": "SUCCESS",
            "data": [{"type": "dict", "data": {"server": {"ServerRunner": "some error"}}}],
        },
    )

    captured = {}
    real_do_command = Session._do_command

    def spy(self, command, *args, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return real_do_command(self, command, *args, **kwargs)

    with patch("nvflare.fuel.flare_api.flare_api.Session._do_command", spy):
        result = call_session(session)

    assert captured["command"] == expected_command
    assert captured["kwargs"] == {"enforce_meta": False}
    # Return shapes differ by command: the `_collect_info` reads return the collected dict,
    # reset_errors returns None, and get_working_directory returns a string. The command and its
    # keywords are what this test is pinning, so only assert the payload where one applies.
    if isinstance(result, dict):
        assert result == {"server": {"ServerRunner": "some error"}}
