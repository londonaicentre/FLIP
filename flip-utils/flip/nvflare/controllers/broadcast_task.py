# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A small reusable workflow for broadcasting lifecycle tasks to clients."""

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import Task
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal


class BroadcastTask(Controller):
    """Broadcast an empty task and wait for every targeted client response."""

    def __init__(self, task_name: str, timeout: int = 600, participating_clients: list[str] | None = None) -> None:
        super().__init__()
        if not task_name:
            raise ValueError("task_name must not be empty")
        if not isinstance(timeout, int):
            raise TypeError(f"timeout must be int but got {type(timeout)}")
        if timeout < 0:
            raise ValueError("timeout must be greater than or equal to 0")

        self.task_name = task_name
        self.timeout = timeout
        self.participating_clients = participating_clients

    def start_controller(self, fl_ctx: FLContext) -> None:
        pass

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        if abort_signal.triggered:
            self.log_info(fl_ctx, f"Abort signal triggered - skipping {self.task_name} task.")
            return

        task = Task(name=self.task_name, data=Shareable(), timeout=self.timeout)
        self.broadcast_and_wait(
            task=task,
            targets=self.participating_clients,
            min_responses=0,
            wait_time_after_min_received=0,
            fl_ctx=fl_ctx,
            abort_signal=abort_signal,
        )

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self.cancel_all_tasks()

    def process_result_of_unknown_task(
        self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext
    ) -> None:
        self.log_warning(fl_ctx, f"Ignoring result for unknown task {task_name!r} from {client.name!r}.")
