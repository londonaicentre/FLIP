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

import traceback

from nvflare.apis.client import Client
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal

from flip.constants import FlipEvents


class InitTraining(Controller):
    def __init__(self):
        """The controller that is executed pre-training and is a part of the FLIP training model.

        The InitTraining workflow notifies the Central Hub that training has initiated.

        It no longer broadcasts a client cleanup task: the retired ``CleanupImages``
        executor wiped the net's imaging directory at job start, which defeated the
        imaging-api download cache across runs. Imaging retention is now owned trust-side
        by imaging-api's TTL sweeper (FLIP#1050).
        """

        super().__init__()

    def start_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Initializing InitTraining workflow.")
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. InitTraining exiting.", fl_ctx)
            return

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Beginning InitTraining control flow phase.")
            self._set_init_training_status(fl_ctx)
        except BaseException as e:
            traceback.print_exc()
            error_msg = f"Exception in InitTraining control_flow: {e}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(str(e), fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Stopping InitTraining controller")
        self.cancel_all_tasks()

    def process_result_of_unknown_task(
        self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext
    ) -> None:
        self.log_error(fl_ctx, "Ignoring result from unknown task.")

    def _set_init_training_status(self, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Attempting to start the step to initialise training...")
            self.fire_event(FlipEvents.TRAINING_INITIATED, fl_ctx)
        except Exception as e:
            traceback.print_exc()
            self.log_error(fl_ctx, str(e))
            self.system_panic(str(e), fl_ctx)
