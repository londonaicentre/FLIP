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
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.app_common.utils.file_utils import resolve_path_under_root
from nvflare.app_common.widgets.validation_json_generator import to_serializable

from flip.constants import PTConstants
from flip.nvflare.components.validation_json_generator import ValidationJsonGenerator


class EvaluationJsonGenerator(ValidationJsonGenerator):
    """Flat-shape evaluation results generator.

    Deduped against FLIP's :class:`ValidationJsonGenerator`: it inherits the manual-dispatch
    mechanism (the no-op ``handle_event`` — ServerEventHandler drives ``handle_evaluation_events``)
    and the numpy-float JSON encoding / path-safe output. It differs only in shape — evaluation
    records one aggregate metrics result per client (``_eval_results[data_client] = metrics``)
    rather than a cross-validation matrix keyed by (data_client, model_owner) — and in the results
    directory / file name.
    """

    def __init__(self, results_dir=PTConstants.EvalDir, json_file_name=PTConstants.EvalResultsFilename):
        """Initialise the evaluation results generator.

        Args:
            results_dir (str): Name of the results directory. Defaults to ``PTConstants.EvalDir``.
            json_file_name (str): Name of the json file. Defaults to ``PTConstants.EvalResultsFilename``.
        """
        super().__init__(results_dir=results_dir, json_file_name=json_file_name)
        # Flat per-client store (not the base's nested self._val_results).
        self._eval_results: dict = {}

    def handle_evaluation_events(self, event_type: str, fl_ctx: FLContext) -> None:
        """FLIP integration point — ServerEventHandler dispatches events here, not via handle_event."""
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
                    if dxo.data_kind == DataKind.METRICS:
                        self._eval_results[data_client] = dxo.data
                    else:
                        self.log_error(
                            fl_ctx, f"Expected dxo of kind METRICS but got {dxo.data_kind} instead.", fire_event=False
                        )
                except Exception:
                    self.log_exception(fl_ctx, "Exception in handling evaluation result.", fire_event=False)
            else:
                self.log_error(fl_ctx, "Evaluation result not found.", fire_event=False)
        elif event_type == EventType.END_RUN:
            run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_job_id())
            res_file_path = resolve_path_under_root(
                run_dir, os.path.join(self._results_dir, self._json_file_name), "evaluation results path"
            )
            eval_res_dir = os.path.dirname(res_file_path)
            if not os.path.exists(eval_res_dir):
                self.log_info(fl_ctx, f"Creating evaluation results directory at {eval_res_dir}")
                os.makedirs(eval_res_dir)
            with open(res_file_path, "w") as f:
                json.dump(self._eval_results, f, indent=4, default=to_serializable)
