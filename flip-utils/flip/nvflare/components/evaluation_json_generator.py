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
import os.path

from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType

from flip.constants import PTConstants


class EvaluationJsonGenerator(FLComponent):
    def __init__(self, results_dir=PTConstants.EvalDir, json_file_name=PTConstants.EvalResultsFilename):
        """Handles EVALUATION_RESULT_RECEIVED event and generates a results.json containing accuracy of each
        validated model.

        Args:
            results_dir (str, optional): Name of the results directory. Defaults to cross_site_eval
            json_file_name (str, optional): Name of the json file. Defaults to evaluation_results.json
        """
        super(EvaluationJsonGenerator, self).__init__()

        self._results_dir = results_dir
        self._eval_results = {}
        self._json_file_name = json_file_name

    def handle_evaluation_events(self, event_type: str, fl_ctx: FLContext):
        if event_type == EventType.START_RUN:
            self._eval_results.clear()
        elif event_type == AppEventType.VALIDATION_RESULT_RECEIVED:
            data_client = fl_ctx.get_prop(AppConstants.DATA_CLIENT, None)
            eval_results = fl_ctx.get_prop(AppConstants.VALIDATION_RESULT, None)

            if not data_client:
                self.log_error(
                    fl_ctx, "data_client unknown. Evaluation result will not be saved to json", fire_event=False
                )

            if eval_results:
                try:
                    dxo = from_shareable(eval_results)
                    dxo.validate()

                    if dxo.data_kind == DataKind.METRICS:
                        if data_client not in self._eval_results:
                            self._eval_results[data_client] = {}
                        self._eval_results[data_client] = dxo.data
                    else:
                        self.log_error(
                            fl_ctx, f"Expected dxo of kind METRICS but got {dxo.data_kind} instead.", fire_event=False
                        )
                except Exception:
                    self.log_exception(fl_ctx, "Exception in handling validation result.", fire_event=False)
            else:
                self.log_error(fl_ctx, "Validation result not found.", fire_event=False)
        elif event_type == EventType.END_RUN:
            run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_job_id())
            eval_res_dir = os.path.join(run_dir, self._results_dir)
            if not os.path.exists(eval_res_dir):
                self.log_info(fl_ctx, f"Creating evaluation results directory at {eval_res_dir}")
                os.makedirs(eval_res_dir)

            res_file_path = os.path.join(eval_res_dir, self._json_file_name)
            with open(res_file_path, "w") as f:
                json.dump(self._eval_results, f, indent=4)
