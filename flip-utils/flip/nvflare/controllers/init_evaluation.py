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

import json
import os
import traceback

from nvflare.apis.client import Client
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal

from flip.constants import FlipEvents


class InitEvaluation(Controller):
    def __init__(self):
        """The controller that is executed pre-evaluation and is a part of the FLIP evaluation model.

        The InitEvaluation workflow notifies the Central Hub that evaluation has initiated and
        validates the evaluation ``config.json``.

        It no longer broadcasts a client cleanup task: the retired ``CleanupImages``
        executor wiped the net's imaging directory at job start, which defeated the
        imaging-api download cache across runs. Imaging retention is now owned trust-side
        by imaging-api's TTL sweeper (FLIP#1050).
        """

        super().__init__()

    def start_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Initializing InitEvaluation workflow.")
        engine = fl_ctx.get_engine()
        if not engine:
            self.system_panic("Engine not found. InitEvaluation exiting.", fl_ctx)
            return

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Beginning InitEvaluation control flow phase...")
            self._set_init_evaluation_status(fl_ctx)

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
        except BaseException as e:
            traceback.print_exc()
            error_msg = f"Exception in InitEvaluation control_flow: {e}"
            self.log_exception(fl_ctx, error_msg)
            self.system_panic(str(e), fl_ctx)

    def stop_controller(self, fl_ctx: FLContext) -> None:
        self.log_info(fl_ctx, "Stopping InitEvaluation controller")
        self.cancel_all_tasks()

    def process_result_of_unknown_task(
        self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext
    ) -> None:
        self.log_error(fl_ctx, "Ignoring result from unknown task.")

    def _set_init_evaluation_status(self, fl_ctx: FLContext) -> None:
        try:
            self.log_info(fl_ctx, "Attempting to start the step to initialise evaluation...")
            self.fire_event(FlipEvents.TASK_INITIATED, fl_ctx)
        except Exception as e:
            traceback.print_exc()
            self.log_error(fl_ctx, str(e))
            self.system_panic(str(e), fl_ctx)
