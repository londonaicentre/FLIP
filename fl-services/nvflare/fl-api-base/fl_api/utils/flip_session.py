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

from nvflare.fuel.flare_api.api_spec import InternalError, SessionClosed
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
        """
        Session.__init__(self, self.username, self.startup_path, self.secure_mode, self._debug, self._study)
        self.try_connect(timeout=5.0)

    def _do_command(self, cmd: str, *args: Any, **kwargs: Any) -> Any:
        """
        Override the _do_command method to add error handling for session inactivity or closure.

        On ``InternalError`` with "session_inactive", reconnects via ``try_connect`` and retries once.
        On ``SessionClosed`` (e.g. idle timeout, fl-server restart, network blip), fully re-initialises
        the admin session via ``_reconnect`` and retries once. Any exception on the retry is logged and
        re-raised immediately — there is no further retry loop.

        ``*args`` / ``**kwargs`` are forwarded untouched so this override stays transparent to the
        base signature (``enforce_meta``, ``props``, and anything a future NVFLARE adds). Accepting
        only ``cmd`` used to break every command that passes them — ``show_errors``, ``show_stats``
        and ``reset_errors``, all of which route through NVFLARE's ``_collect_info``
        (``enforce_meta=False``) — while leaving the positional-only callers working, so the fault
        stayed hidden until someone called one of those three (FLIP#1032).

        Args:
            cmd (str): The command to be executed.
            *args (Any): Positional arguments forwarded to the base implementation.
            **kwargs (Any): Keyword arguments forwarded to the base implementation.
        """
        try:
            return super()._do_command(cmd, *args, **kwargs)
        except InternalError as e:
            if "session_inactive" in str(e):
                logger.warning("Session inactive, trying to reconnect...")
                self.try_connect(timeout=5.0)
                return super()._do_command(cmd, *args, **kwargs)
            raise e
        except SessionClosed:
            logger.warning("Session closed; attempting to reconnect and retry command.")
            self._reconnect()
            try:
                return super()._do_command(cmd, *args, **kwargs)
            except Exception:
                logger.error("Retry after reconnect failed for command: %s", cmd)
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

        Body-identical to the base implementation (which is also
        ``self.get_system_info().client_info``); it exists only to re-declare the return type now
        that ``get_system_info`` above yields FLIP models. Same duck-typing caveat as that method.

        Returns:
            List[ClientInfoModel]: a list of ClientInfoModel objects containing name, last connect time, and status.
        """
        return self.get_system_info().client_info
