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
from nvflare.fuel.flare_api.api_spec import (
    ClientInfo,
    InternalError,
    NoConnection,
    ServerInfo,
    SessionClosed,
    SystemInfo,
)
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
    s = FLIP_Session(username="u", startup_path="p", secure_mode=False, debug=False)
    # Most tests exercise post-connect behaviour; the lazy first-use connect is tested separately.
    s._connected = True
    return s


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


def test_do_command_lazy_connects_when_not_connected(session):
    """✅ Never-connected session (PER_JOB_FL_SERVER boot) reconnects before first command."""
    session._connected = False
    with (
        patch("nvflare.fuel.flare_api.flare_api.Session._do_command", return_value="ok") as parent_do_command,
        patch.object(session, "_reconnect") as mock_reconnect,
    ):
        result = session._do_command("CMD")

    mock_reconnect.assert_called_once_with()
    parent_do_command.assert_called_once_with("CMD")
    assert result == "ok"


def test_do_command_retries_once_on_no_connection(session):
    """✅ NoConnection (server went away) → full reconnect + retry once."""
    with (
        patch(
            "nvflare.fuel.flare_api.flare_api.Session._do_command",
            side_effect=[NoConnection("cannot connect to server"), "ok"],
        ) as parent_do_command,
        patch.object(session, "_reconnect") as mock_reconnect,
    ):
        result = session._do_command("CMD")

    mock_reconnect.assert_called_once_with()
    assert parent_do_command.call_count == 2
    assert result == "ok"


def test_check_server_status_reports_stopped_when_unreachable(session):
    with patch.object(session, "get_system_info", side_effect=NoConnection("cannot connect to server")):
        out = session.check_server_status()

    assert isinstance(out, ServerInfoModel)
    assert out.status == "STOPPED"


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


def _assert_substitutable(base_sig: inspect.Signature, override_sig: inspect.Signature, label: str) -> None:
    """Assert `override_sig` accepts every call the base accepts, by name *and* by position.

    Split out from the test that drives it so the rule itself can be pinned against synthetic
    signatures (below). The rule is code too, and has shipped with holes twice (FLIP#1032) --
    re-mutating `FLIP_Session` by hand is not a check CI can repeat.

    Args:
        base_sig (inspect.Signature): Signature of the NVFLARE method being overridden.
        override_sig (inspect.Signature): Signature of the `FLIP_Session` override.
        label (str): How to name the override in assertion messages.
    """
    override_params = override_sig.parameters
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in override_params.values())
    accepts_var_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in override_params.values())
    positional = [
        p
        for p in override_params.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    for index, (name, param) in enumerate(base_sig.parameters.items()):
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        override_param = override_params.get(name)

        # Reachable BY NAME. `**kwargs` alone is not enough for a *renamed* positional (the old
        # `cmd` for the base's `command`): it swallows the value positionally but still breaks
        # `_do_command(command=...)`.
        if override_param is None:
            # Absent by name is only acceptable when **kwargs can carry it AND the base gives it
            # a default -- a required base parameter must always be nameable.
            reachable = accepts_var_kw and param.default is not inspect.Parameter.empty
            assert reachable, (
                f"{label} does not accept base parameter '{name}' ({override_sig}); "
                f"narrowing a base signature breaks NVFLARE's own callers."
            )
        elif param.default is not inspect.Parameter.empty:
            # A silently different default is the quieter half of the same bug: the removed
            # __init__ flipped secure_mode from the base's True to False, which no
            # parameter-presence check would notice.
            assert override_param.default == param.default, (
                f"{label} changes the default of '{name}' from {param.default!r} to "
                f"{override_param.default!r}; callers relying on the base default silently get "
                f"different behaviour."
            )

        if param.kind not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            continue

        # ...and reachable BY POSITION, in the base's own slot. Keeping the name while demoting the
        # parameter to keyword-only reads as harmless and is not: NVFLARE passes `command`
        # positionally from all eight of its `_do_command` call sites.
        stays_positional = override_param is None or override_param.kind is not inspect.Parameter.KEYWORD_ONLY
        assert stays_positional, (
            f"{label} makes base parameter '{name}' keyword-only ({override_sig}); "
            f"callers passing it positionally would raise TypeError."
        )
        if index < len(positional):
            assert positional[index].name == name, (
                f"{label} does not put base parameter '{name}' in the base's positional slot "
                f"{index} ({override_sig}); positional callers would bind the wrong value."
            )
        else:
            # Past the override's own positionals the slot exists only if `*args` absorbs it.
            assert accepts_var_positional, (
                f"{label} has no positional slot {index} for base parameter '{name}' "
                f"({override_sig}); positional callers would raise TypeError."
            )


@pytest.mark.parametrize("method_name", _overridden_methods())
def test_override_does_not_narrow_the_base_signature(method_name):
    """Every parameter the base accepts must be accepted by our override.

    `_do_command` used to take only `cmd`, so NVFLARE's `_collect_info` calls
    (`enforce_meta=False`) -- show_errors, show_stats, reset_errors -- raised TypeError.
    """
    _assert_substitutable(
        inspect.signature(getattr(Session, method_name)),
        inspect.signature(getattr(FLIP_Session, method_name)),
        f"FLIP_Session.{method_name}",
    )


# --------------------------------------------------------------------------------------
# Guard the guard: `_assert_substitutable` against signatures known to be substitutable or
# not. `_base_shape` mirrors NVFLARE's `_do_command(self, command, enforce_meta=True,
# props=None)`, the signature whose narrowing caused FLIP#1032.
# --------------------------------------------------------------------------------------


def _base_shape(self, command, enforce_meta=True, props=None):
    """Stand-in for the NVFLARE base signature the cases below are checked against."""


@pytest.mark.parametrize(
    ("shape", "override"),
    [
        ("forwards everything it does not name", lambda self, command, *args, **kwargs: None),
        ("restates the base exactly", lambda self, command, enforce_meta=True, props=None: None),
        ("adds a trailing parameter of its own", lambda self, command, *args, retries=3, **kwargs: None),
    ],
)
def test_substitutable_shapes_pass_the_guard(shape, override):
    """A widening override must not be flagged -- a guard that fails these is unusable."""
    _assert_substitutable(inspect.signature(_base_shape), inspect.signature(override), shape)


@pytest.mark.parametrize(
    ("shape", "override", "message"),
    [
        # The hole this pins: same name, still reachable by keyword, but no longer positional.
        (
            "demotes a positional to keyword-only",
            lambda self, *args, command, **kwargs: None,
            "makes base parameter 'command' keyword-only",
        ),
        (
            "renames the first positional",
            lambda self, cmd, *args, **kwargs: None,
            "does not accept base parameter 'command'",
        ),
        (
            "drops a middle parameter, shifting a later one up",
            lambda self, command, props, **kwargs: None,
            "in the base's positional slot 2",
        ),
        (
            "leaves no slot for the base's trailing positionals",
            lambda self, command, **kwargs: None,
            "has no positional slot 2",
        ),
        (
            "flips a default",
            lambda self, command, *args, enforce_meta=False, **kwargs: None,
            "changes the default of 'enforce_meta'",
        ),
    ],
)
def test_narrowing_shapes_fail_the_guard(shape, override, message):
    """Each of these breaks a call the base allows, so the guard must reject it."""
    with pytest.raises(AssertionError, match=message):
        _assert_substitutable(inspect.signature(_base_shape), inspect.signature(override), shape)


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


def test_system_info_models_accept_the_missing_times_nvflare_reports():
    """The widened fields take None and print "unknown" for it, as NVFLARE's own types do.

    A stopped or restarting server reports no start time (the admin meta is read with a bare
    ``get``), and a registered client that has never connected has no last connect time
    (``_wait_for_clients_restart`` skips it with ``if previous_time is None``). Requiring floats
    here raised ``ValidationError`` on both, inside NVFLARE's own restart and shutdown waits.
    """
    server = ServerInfoModel(status="stopped")
    client = ClientInfoModel(name="c1", status="registered")

    assert server.start_time is None
    assert client.last_connect_time is None
    assert str(server) == str(ServerInfo(status="stopped", start_time=None))
    assert "unknown" in str(client)


def test_system_info_models_print_a_real_time_when_they_have_one():
    """The "unknown" branch is for None only -- a falsy-but-real timestamp must still print as a time."""
    server = ServerInfoModel(status="running", start_time=0.0)
    client = ClientInfoModel(name="c1", last_connect_time=0.0, status="online")

    assert "unknown" not in str(server)
    assert "unknown" not in str(client)


def test_get_system_info_survives_the_missing_times_nvflare_reports(session):
    """The type swap must return a usable object exactly where the base does.

    Only the base call is faked, with NVFLARE's own ``SystemInfo`` shape carrying the two fields
    unset -- what a restarting server and a never-connected client produce. Before the fields were
    widened this raised ``ValidationError`` from every path that reaches ``get_system_info``: the
    four FL API routes, and NVFLARE's ``restart`` and client-shutdown waits.
    """
    upstream = SystemInfo(
        server_info=ServerInfo(status="starting", start_time=None),
        client_info=[ClientInfo(name="c1", last_connect_time=None)],
        job_info=[],
    )

    with patch("nvflare.fuel.flare_api.flare_api.Session.get_system_info", return_value=upstream):
        info = session.get_system_info()

    assert isinstance(info, SystemInfoModel)
    assert info.server_info.start_time is None
    assert info.client_info[0].last_connect_time is None


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

    With no override, ``FLIP_Session.__init__`` resolves to ``Session.__init__`` itself, so today
    this pins the base's own contract -- the ``study`` keyword and ``secure_mode=True`` default that
    ``_reconnect`` forwards by keyword -- and only starts exercising FLIP code if an override is
    reintroduced.
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
