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


from typing import Any

from nvflare.fuel.flare_api.api_spec import InternalError, NoConnection, SessionClosed
from nvflare.fuel.flare_api.flare_api import Session

from fl_api.utils.logger import logger
from fl_api.utils.schemas import ClientInfoModel, JobInfoModel, ServerInfoModel, SystemInfoModel


class FLIP_Session(Session):
    """NVFLARE admin ``Session`` with reconnect-on-drop, plus FLIP-shaped system info.

    There is deliberately **no** ``__init__`` override. The base already stores everything this
    subclass needs (``username``, ``startup_path``, ``secure_mode``, ``_debug``, ``_study``), so an
    override could only re-stash them under private aliases — which is what it used to do, at the
    cost of dropping the base's ``study`` parameter and flipping the ``secure_mode`` default from
    ``True`` to ``False`` (FLIP#1032).

    **Rule for anything added here: never narrow a base signature.** Python dispatches to this
    class, so a parameter the base accepts and this class does not is a ``TypeError`` at whichever
    upstream call site passes it — a failure mode invisible until that one path is exercised.
    ``test_flip_session.py`` pins this for every override.
    """

    def _reconnect(self) -> None:
        """Re-initialise the underlying admin API and log in again after the session was closed.

        Reads the connection parameters back off the base class rather than keeping private
        copies. ``_study`` is passed through deliberately: re-initialising without it would
        silently drop the session back to the default study, changing which jobs subsequent
        commands can see.

        The previous ``AdminAPI`` is closed **first**. ``Session.__init__`` assigns a brand-new
        one over ``self.api``, so without this the old object is merely dereferenced: its cell
        threads and sockets keep running and the FL server still counts the session as logged in.
        A flapping fl-server drives one reconnect per command, so those abandoned sessions
        accumulate for the lifetime of this process (FLIP#1035). ``_close_ignore_errors`` is
        NVFLARE's own helper for exactly this — it swallows the logout failure you get when the
        server has already gone, while still stopping the local machinery. Closing *after*
        re-initialising would tear down the session just built.
        """
        self._close_ignore_errors()
        Session.__init__(
            self,
            username=self.username,
            startup_path=self.startup_path,
            secure_mode=self.secure_mode,
            debug=self._debug,
            study=self._study,
        )
        try:
            self.try_connect(timeout=5.0)
        except Exception:
            # `Session.__init__` above has already swapped in a fresh AdminAPI, so a failure here
            # leaves the session holding one that was never logged in: `closed` is False, but its
            # command registry is empty because only `_after_login` fills it. Every later command
            # then fails client-side in `_get_command_detail` with
            # `ERROR_SYNTAX: Command <name> not found` — an InternalError no recovery branch
            # catches, and `closed` being False means the base never raises SessionClosed either.
            # The session looks healthy and can never work again; only a container restart clears
            # it. Closing the half-built API restores the invariant "unusable implies closed", so
            # the next command raises SessionClosed and recovery runs again, self-healing once the
            # server is back. `api.close()` rather than `_close_ignore_errors()`: no logout
            # round-trip to a server we already know is unreachable.
            logger.warning("Reconnect failed; closing the half-built session so the next command retries.")
            try:
                self.api.close()
            except Exception:
                pass
            raise

    def _do_command(self, command: str, *args: Any, **kwargs: Any) -> Any:
        """
        Override the _do_command method to add error handling for session inactivity or closure.

        Three ways a live session goes bad, all recovered here (FLIP#1035):

        * ``InternalError`` with "session_inactive" — reconnect via ``try_connect``, and if the
          AdminAPI turns out to be closed or unreachable, fall through to a full ``_reconnect``.
        * ``SessionClosed`` — idle timeout, or the API was closed under us.
        * ``NoConnection`` — the fl-server went away (restart, network blip). This one is easy to
          miss: it derives from ``ConnectionError``/``OSError``, **not** from ``SessionClosed`` or
          ``InternalError``, so it was previously caught by neither branch and escaped to the
          router. Observed live: restarting ``fl-server-net-N`` left every subsequent command
          answering 500 with ``cannot connect to server: ERROR_SERVER_CONNECTION`` **permanently**,
          because nothing rebuilt the AdminAPI — only restarting the fl-api container cleared it.

        Either way the command is retried exactly once; any exception on the retry is logged and
        re-raised immediately — there is no further retry loop.

        ``*args`` / ``**kwargs`` are forwarded untouched so this override stays transparent to the
        base signature (``enforce_meta``, ``props``, and anything a future NVFLARE adds). The first
        parameter is named ``command`` to match the base exactly: forwarding does not repair a
        renamed positional, so ``_do_command(command=...)`` would still be a ``TypeError``.

        Accepting only ``cmd`` used to break every caller that passes those keywords. NVFLARE 2.8.0
        has **eight** such sites — ``_shell_command_on_target``, ``_collect_info``,
        ``report_resources``, ``report_version``, ``get_job_logs``, ``configure_job_log``,
        ``configure_site_log`` and ``do_app_command`` — of which four are reachable from this
        service's routes: ``show_errors``, ``show_stats`` and ``reset_errors`` (via
        ``_collect_info``) plus ``get_working_directory`` (via ``_shell_command_on_target``). Every
        other caller passes the command positionally with defaults, which the narrowed signature
        happened to satisfy, so the fault stayed hidden until someone called one of those four
        (FLIP#1032).

        Args:
            command (str): The command to be executed.
            *args (Any): Positional arguments forwarded to the base implementation.
            **kwargs (Any): Keyword arguments forwarded to the base implementation.
        """
        try:
            return super()._do_command(command, *args, **kwargs)
        except InternalError as e:
            if "session_inactive" not in str(e):
                raise
            logger.warning("Session inactive, trying to reconnect...")
            try:
                self.try_connect(timeout=5.0)
            except (SessionClosed, NoConnection):
                # try_connect refuses outright when the AdminAPI is closed, and raises
                # NoConnection when the server is unreachable. Raised in here, neither re-enters
                # this try's handler list, so they used to escape as a 500 and the rebuild below
                # never ran — the branches recovered from the same condition with very different
                # strength (FLIP#1035).
                logger.warning("Reconnect refused; rebuilding the admin session.")
                self._reconnect()
            return self._retry_after_recovery(command, *args, **kwargs)
        except (SessionClosed, NoConnection):
            logger.warning("Session unusable; attempting to reconnect and retry command.")
            self._reconnect()
            return self._retry_after_recovery(command, *args, **kwargs)

    def _retry_after_recovery(self, command: str, *args: Any, **kwargs: Any) -> Any:
        """Re-issue a command once after a reconnect, logging what failed if it still cannot run.

        Args:
            command (str): The command to re-issue, unchanged.
            *args (Any): Positional arguments forwarded to the base implementation.
            **kwargs (Any): Keyword arguments forwarded to the base implementation.

        Returns:
            Any: The base implementation's reply.

        Raises:
            Exception: Whatever the retry raised — there is no second retry.
        """
        try:
            return super()._do_command(command, *args, **kwargs)
        except Exception:
            logger.error("Retry after reconnect failed for command: %s", command)
            raise

    def check_server_status(self) -> ServerInfoModel:
        """
        Checks the status of the server.

        NOTE that this API considers one server only. For multiple servers systems, this function should accommodate a
        list of servers as argument, similar to how the client status is handled.

        Returns:
            ServerInfoModel: a ServerInfoModel object containing the server status and start time.
        """
        return self.get_system_info().server_info

    def check_client_status(self, target: list[str] | None = None) -> list[ClientInfoModel]:
        """
        Check status of every client or specific clients.

        Args:
            target (List[str]): list of client names to check status for. If empty, all clients will be returned.

        Returns:
            List[fl_api.utils.schemas.ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect
            time, and status.
        """
        system_info = self.get_system_info()
        if target:
            clients_info = [client for client in system_info.client_info if client.name in target]
        else:
            clients_info = system_info.client_info

        # Convert response to ClientInfoModel objects
        clients = [
            ClientInfoModel(name=c.name, last_connect_time=c.last_connect_time, status="not set") for c in clients_info
        ]

        # Also get client status
        for client in clients:
            client_job_status = self.get_client_job_status([client.name])
            assert len(client_job_status) == 1, "Expected only one job status for client {}".format(client.name)
            client_job_status = client_job_status[0]
            logger.info(f"client_job_status: {client_job_status}")
            client.status = client_job_status.get("status", "unknown")

        return clients

    def get_system_info(self) -> SystemInfoModel:
        """
        Get system info of the FL system, as FLIP's serialisable schema.

        **This shadows a base method NVFLARE calls internally**, and returns a different type
        (``SystemInfoModel`` rather than NVFLARE's ``SystemInfo``). Keeping the base's name is a
        deliberate trade — the FL API's ``/get_system_info`` route serialises the result directly —
        but it means NVFLARE's own callers get this object instead of theirs. They are
        ``_client_last_connect_times``, ``_wait_for_clients_shutdown``, ``_wait_for_clients_restart``,
        ``restart`` and ``get_connected_client_list``, and between them they read exactly four
        attributes:

        * ``server_info.status``
        * ``server_info.start_time``
        * ``client_info[].name``
        * ``client_info[].last_connect_time``

        That is the whole contract this substitution rests on. It is duck-typed, so nothing enforces
        it at runtime — ``test_flip_session.py`` pins those four attributes instead. **Renaming or
        dropping any of them on the models breaks NVFLARE's restart and client-shutdown waits
        silently**, which is why the pin exists. Widen the models rather than reshape them.

        Returns:
            SystemInfoModel: system info of the FL system.
        """
        info = super().get_system_info()
        system_info = SystemInfoModel(
            server_info=ServerInfoModel(status=info.server_info.status, start_time=info.server_info.start_time),
            client_info=[
                ClientInfoModel(name=c.name, last_connect_time=c.last_connect_time, status="not set")
                for c in info.client_info
            ],
            job_info=[JobInfoModel(job_id=j.job_id, app_name=j.app_name) for j in info.job_info],
        )
        return system_info

    def get_connected_client_list(self) -> list[ClientInfoModel]:
        """
        Get a list of the connected clients, as FLIP's serialisable schema.

        Delegates to the base rather than re-implementing its body, so a future upstream change to
        how "connected" is derived is inherited rather than silently diverging here. The override
        exists only to re-declare the return type, now that ``get_system_info`` above yields FLIP
        models. Same duck-typing caveat as that method.

        Returns:
            List[ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect time, and status.
        """
        return super().get_connected_client_list()
