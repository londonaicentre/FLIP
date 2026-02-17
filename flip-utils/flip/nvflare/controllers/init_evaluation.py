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

import json
import os
import traceback

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import ClientTask, Task
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal
from nvflare.app_common.app_constant import AppConstants

from flip import FLIP
from flip.constants import FlipConstants, FlipEvents, FlipTasks, ModelStatus
from flip.utils import Utils


class InitEvaluation(Controller):
    def __init__(
        self,
        model_id: str,
        min_clients: int = FlipConstants.MIN_CLIENTS,
        flip: FLIP = FLIP(),
        cleanup_timeout: int = 600,
    ):
        """The controller that is executed pre-training and is a part of the FLIP training model

        The InitTraining workflow sends a request to the Central Hub, stating that training has initiated
        and executes the client cleanup task.

        Args:
            model_id (str): ID of the model that the training is being performed under.
            min_clients (int, optional): Minimum number of clients. Defaults to 1 for the aggregation to take place with
                successful results.
            cleanup_timeout (int, optional): Timeout for image cleanup, defaults to 600 seconds (10 minutes)

        Raises:
           ValueError:
            - when the model ID is not a valid UUID.
            - when the minimum number of clients specified is less than 1
            - when cleanup_timeout is less the 0
        """

        super().__init__()

        try:
            if Utils.is_valid_uuid(model_id) is False:
                raise ValueError(f"The model ID: {model_id} is not a valid UUID")

            if min_clients < FlipConstants.MIN_CLIENTS:
                raise ValueError(
                    f"Invalid number of minimum clients specified. {min_clients} is less than "
                    f"{FlipConstants.MIN_CLIENTS} which is the minimum number for a successful aggregation"
                )

            if cleanup_timeout < 0:
                raise ValueError("cleanup_timeout must be greater than or equal to 0.")
        except ValueError as e:
            flip.update_status(model_id, ModelStatus.ERROR)
            raise ValueError(e)

        self._model_id = model_id
        self._min_clients = min_clients
        self.flip = flip
        self._cleanup_timeout = cleanup_timeout

    def start_controller(self, fl_ctx: FLContext):
        self.log_info(fl_ctx, "Initializing InitTraining workflow.")
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. InitTraining exiting.", fl_ctx)
            return

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext):
        try:
            self.log_info(fl_ctx, "Beginning InitEvaluation control flow phase...")
            self._set_init_evaluation_status(fl_ctx)

            self.log_info(fl_ctx, "Beginning initial evaluation cleanup task...")

            cleanup_task = Task(
                name=FlipTasks.INIT_TASK.value,
                data=Shareable(),
                timeout=self._cleanup_timeout,
                result_received_cb=self._process_cleanup_result,
            )

            self.broadcast_and_wait(
                task=cleanup_task,
                min_responses=self._min_clients,
                wait_time_after_min_received=0,
                fl_ctx=fl_ctx,
                abort_signal=abort_signal,
            )

            self.log_info(fl_ctx, "Initial cleanup task completed")
            self.log_info(fl_ctx, "Checking config.json file...")

            # Look for config.json in the custom directory where user files are placed
            engine = fl_ctx.get_engine()
            job_id = fl_ctx.get_job_id()
            app_root = engine.get_workspace().get_app_dir(job_id)
            config_path = os.path.join(app_root, "custom", "config.json")

            if not os.path.isfile(config_path):
                self.log_error(fl_ctx, f"config.json is a mandatory file at: {config_path}")
            with open(config_path, "r") as file:
                config = json.load(file)
            if "models" not in config.keys():
                self.log_error(
                    fl_ctx,
                    "In the evaluation pipeline, a models key has to be present in config.json, "
                    "mapping the models to their checkpoint and path.",
                )
            for key, model_info in config["models"].items():
                if "checkpoint" not in model_info or "path" not in model_info:
                    self.log_error(
                        fl_ctx,
                        "Each model in config.json must have 'checkpoint' and 'path' keys."
                        f"Issue found in model: {key}.",
                    )

                    self.fire_event(EventType.END_RUN, fl_ctx)

            if self._check_abort_signal(fl_ctx, abort_signal):
                return
        except BaseException as e:
            traceback.print_exc()
            error_msg = f"Exception in InitTraining control_flow: {e}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(str(e), fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Stopping InitTraining controller")
        self.cancel_all_tasks()

    def process_result_of_unknown_task(
        self, client: Client, task_name, client_task_id, result: Shareable, fl_ctx: FLContext
    ) -> None:
        self.log_error(fl_ctx, "Ignoring result from unknown task.")

    def _set_init_evaluation_status(self, fl_ctx: FLContext):
        try:
            self.log_info(fl_ctx, "Attempting to start the step to initialise evaluation...")
            self.fire_event(FlipEvents.TASK_INITIATED, fl_ctx)
        except Exception as e:
            traceback.print_exc()
            self.log_error(fl_ctx, str(e))
            self.system_panic(str(e), fl_ctx)

    def _check_abort_signal(self, fl_ctx, abort_signal: Signal):
        if abort_signal.triggered:
            self._phase = AppConstants.PHASE_FINISHED
            self.log_info(fl_ctx, "Abort signal received.")
            self.fire_event(FlipEvents.ABORTED, fl_ctx)
            return True
        return False

    def _process_cleanup_result(self, client_task: ClientTask, fl_ctx: FLContext):
        result = client_task.result
        client_name = client_task.client.name

        self._accept_cleanup_result(client_name=client_name, result=result, fl_ctx=fl_ctx)

        client_task.result = None

    def _accept_cleanup_result(self, client_name: str, result: Shareable, fl_ctx: FLContext):
        rc = result.get_return_code()

        if rc and rc == ReturnCode.OK:
            return

        if rc in [ReturnCode.EXECUTION_EXCEPTION, ReturnCode.TASK_UNKNOWN]:
            formatted_exception = result.get_header("exception")

            if formatted_exception is not None:
                self.log_error(fl_ctx, formatted_exception)
                self.flip.send_handled_exception(
                    formatted_exception=formatted_exception, client_name=client_name, model_id=self._model_id
                )

            self.system_panic("Execution Exception initiating client training. InitTraining exiting.", fl_ctx=fl_ctx)
            return False
