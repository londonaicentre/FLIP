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
from pathlib import Path
from typing import Dict, List, Tuple

from nvflare.apis.dxo import from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.security.logging import secure_format_traceback

from flip.constants import PTConstants


class MetricsValidator:
    def __init__(self, input_evaluation: Dict, input_models: List):
        self.input_evaluation = input_evaluation
        self.input_models = input_models

    def validate(self, input_evaluation) -> Tuple[bool, str]:
        for model, evaluation in input_evaluation.items():
            if model not in self.input_models:
                return False, f"Model '{model}' is not in the list of input models."
            else:
                if not isinstance(evaluation, dict):
                    return False, "Each model must be mapped to a dictionary of metrics."
                success, message = self.validate_element(evaluation, self.input_evaluation)
                if not success:
                    return success, message
        return True, "Successfully validated all models."

    def validate_element(self, element, original_element) -> Tuple[bool, str]:
        for key, value in element.items():
            if key not in original_element.keys():
                return False, "Wrong metric in evaluation."
            else:
                if isinstance(value, dict):
                    success, message = self.validate_element(value, original_element[key])
                    if not success:
                        return success, message
                else:
                    if not isinstance(value, (float, list)):
                        return False, f"Metric '{key}' must be a float or a list of floats."
                    if isinstance(value, list):
                        if not all(isinstance(x, float) for x in value):
                            return False, f"All elements in the list for metric '{key}' must be floats."
        return True, "Successfully validated elements."


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

            # We load the config
            metrics_validator = None
            config_path = working_dir / "config.json"
            if config_path.exists():
                with open(config_path, "r") as f:
                    config_content = json.load(f)
                metrics_validator = MetricsValidator(
                    input_evaluation=config_content["evaluation_output"],
                    input_models=list(config_content["models"].keys()),
                )

            # Model weights are loaded in the server, and shouldn't be available in the client side.
            weight_files = [i for i in os.listdir(working_dir) if ".pt" in i or ".pth" in i]
            for wf in weight_files:
                self.log_info(fl_ctx, f"Removing unsafe pytorch file at: {wf} from the client application folder.")
                os.remove(os.path.join(working_dir, wf))
            output = self._evaluator.execute(task_name, shareable, fl_ctx, abort_signal)
            output_dxo = from_shareable(output)

            # Validate output if metrics_validator was created
            if metrics_validator is not None:
                try:
                    success, message = metrics_validator.validate(input_evaluation=output_dxo.data)
                    if not success:
                        self.log_error(fl_ctx, f"Could not load output properly: {message}.")
                except Exception as e:
                    self.log_error(fl_ctx, f"The output validation failed with exception: {type(e).__name__}: {str(e)}")
                    self.log_error(fl_ctx, f"Output data: {output_dxo.data}")

            return output

        except Exception:
            self.log_info(fl_ctx, "An exception has been caught in the FLIP_EVALUATOR")

            formatted_exception = secure_format_traceback()

            self.log_error(fl_ctx, formatted_exception)

            return make_reply(ReturnCode.EXECUTION_EXCEPTION, headers={"exception": formatted_exception})
