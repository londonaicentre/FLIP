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

import os
from pathlib import Path

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.security.logging import secure_format_traceback

from flip.constants import PTConstants


class RUN_EVALUATOR(Executor):
    """Executes the uploaded evaluator and handles any errors."""

    def __init__(self, evaluate_task_name=PTConstants.EvalTaskName, project_id="", query=""):
        super(RUN_EVALUATOR, self).__init__()

        self._evaluate_task_name = evaluate_task_name
        self._project_id = project_id
        self._query = query
        self._evaluator = None

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        try:
            if self._evaluator is None:
                # Lazy import to avoid importing user's evaluator.py at module load time
                # This allows standard/fed_opt jobs (which don't have evaluator.py) to import flip.executors
                from evaluator import FLIP_EVALUATOR as UPLOADED_EVALUATOR

                self._evaluator = UPLOADED_EVALUATOR(
                    evaluate_task_name=PTConstants.EvalTaskName,
                    project_id=self._project_id,
                    query=self._query,
                )

            # working_dir should be current directory where the job runs, not the flip package location
            working_dir = Path.cwd()

            # Model weights are loaded in the server, and shouldn't be available in the client side.
            weight_files = [i for i in os.listdir(working_dir) if ".pt" in i or ".pth" in i]
            for wf in weight_files:
                self.log_info(fl_ctx, f"Removing unsafe pytorch file at: {wf} from the client application folder.")
                os.remove(os.path.join(working_dir, wf))

            return self._evaluator.execute(task_name, shareable, fl_ctx, abort_signal)

        except Exception:
            self.log_info(fl_ctx, "An exception has been caught in the FLIP_EVALUATOR")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
